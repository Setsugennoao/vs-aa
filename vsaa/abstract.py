from dataclasses import dataclass
from dataclasses import field as dc_field
from itertools import zip_longest
from math import ceil, log2
from typing import Any

import vapoursynth as vs
from vskernels import Catrom, Kernel
from vskernels.kernels import Scaler

core = vs.core


class _SingleInterpolate:
    _shift: float

    def _interpolate(self, clip: vs.VideoNode, double_y: bool, **kwargs: Any) -> vs.VideoNode:
        raise NotImplementedError


@dataclass
class _Antialiaser(_SingleInterpolate):
    field: int = dc_field(default=0, kw_only=True)
    transpose_first: bool = dc_field(default=False, kw_only=True)
    shifter: Kernel = dc_field(default=Catrom(), kw_only=True)

    def preprocess_clip(self, clip: vs.VideoNode) -> vs.VideoNode:
        return clip

    def get_aa_args(self, clip: vs.VideoNode, **kwargs: Any) -> dict[str, Any]:
        return {}


class _FullInterpolate(_SingleInterpolate):
    def _full_interpolate_enabled(self, x: bool, y: bool) -> bool:
        return False

    def _full_interpolate(self, clip: vs.VideoNode, double_y: bool, double_x: bool, **kwargs: Any) -> vs.VideoNode:
        raise NotImplementedError


class SuperSampler(_Antialiaser, Scaler):
    def get_ss_args(self, clip: vs.VideoNode, **kwargs: Any) -> dict[str, Any]:
        return {}

    def scale(
        self, clip: vs.VideoNode, width: int, height: int, shift: tuple[float, float] = (0, 0), **kwargs: Any
    ) -> vs.VideoNode:
        clip = self.preprocess_clip(clip)

        assert clip.format

        kwargs = self.get_aa_args(clip, **kwargs) | self.get_ss_args(clip, **kwargs) | kwargs

        divw, divh = (ceil(size) for size in (width / clip.width, height / clip.height))

        mult_x, mult_y = (int(log2(divs)) for divs in (divw, divh))

        cdivw, cdivh = 1 << clip.format.subsampling_w, 1 << clip.format.subsampling_h

        if ((divw < 1) or (divw < 1)) or divw == divh == 1:
            raise ValueError(f'{self.__class__.__name__}.scale: width and height must be bigger than clip\'s size.')

        upscaled = clip

        def _transpose(before: bool, is_width: int, y: int, x: int) -> None:
            nonlocal upscaled

            before = self.transpose_first if before else not self.transpose_first

            if ((before or not y) if is_width else (before and x)):
                upscaled = upscaled.std.Transpose()

        for (y, x) in zip_longest([True] * mult_y, [True] * mult_x, fillvalue=False):
            if isinstance(self, _FullInterpolate) and self._full_interpolate_enabled(x, y):
                upscaled = self._full_interpolate(upscaled, y, x, **kwargs)
            else:
                for isx, val in enumerate([y, x]):
                    if val:
                        _transpose(True, isx, y, x)

                        upscaled = self._interpolate(upscaled, True, **kwargs)

                        _transpose(False, isx, y, x)

            topshift = leftshift = cleftshift = ctopshift = 0.0

            if y and self._shift:
                topshift = ctopshift = self._shift

                if cdivw == 2 and cdivh == 2:
                    ctopshift -= 0.125
                elif cdivw == 1 and cdivh == 2:
                    ctopshift += 0.125

            if x and self._shift:
                leftshift = cleftshift = self._shift

                if cdivw in {4, 2} and cdivh in {4, 2, 1}:
                    cleftshift = self._shift + 0.5

                    if cdivw == 4 and cdivh == 1:
                        cleftshift -= 0.125 * 1
                    elif cdivw == 2 and cdivh == 2:
                        cleftshift -= 0.125 * 2
                    elif cdivw == 2 and cdivh == 1:
                        cleftshift -= 0.125 * 3

            upscaled = self.shifter.shift(
                upscaled, [topshift, ctopshift], [leftshift, cleftshift]
            )

        return self.shifter.scale(upscaled, width, height, shift)


class SingleRater(_Antialiaser):
    def get_sr_args(self, clip: vs.VideoNode, **kwargs: Any) -> dict[str, Any]:
        return {}

    def aa(self, clip: vs.VideoNode, y: bool = True, x: bool = False, **kwargs: Any) -> vs.VideoNode:
        clip = self.preprocess_clip(clip)

        kwargs = self.get_aa_args(clip, **kwargs) | self.get_sr_args(clip, **kwargs) | kwargs

        upscaled = clip

        def _transpose(before: bool, is_width: int) -> None:
            nonlocal upscaled

            before = self.transpose_first if before else not self.transpose_first

            if ((before or not y) if is_width else (before and x)):
                upscaled = upscaled.std.Transpose()

        for isx, val in enumerate([y, x]):
            if val:
                _transpose(True, isx)

                if isinstance(self, _FullInterpolate) and self._full_interpolate_enabled(x, y):
                    upscaled = self._full_interpolate(upscaled, False, False, **kwargs)
                else:
                    upscaled = self._interpolate(upscaled, False, **kwargs)

                _transpose(False, isx)

        return upscaled


class DoubleRater(SingleRater):
    def aa(self, clip: vs.VideoNode, y: bool = True, x: bool = False, **kwargs: Any) -> vs.VideoNode:
        clip = self.preprocess_clip(clip)

        original_field = int(self.field)

        self.field = 0
        aa0 = super().aa(clip, y, x, **kwargs)
        self.field = 1
        aa1 = super().aa(clip, y, x, **kwargs)

        self.field = original_field

        return aa0.std.Merge(aa1)


class Antialiaser(DoubleRater, SuperSampler):
    ...
