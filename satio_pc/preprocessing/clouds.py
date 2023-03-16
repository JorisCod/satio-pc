"""
SCL mask postprocessing

The SCL mask is binarized according to a list of given values.
The binary mask is then eroded to get rid of spurious pixels coming
from bright buildings or dark features. We assume clouds to be at least larger
than few pixels.
After erosion we dilate the mask as most often cloud borders are not flagged
correctly.
"""
from dataclasses import dataclass

import dask
import dask.array as da
import xarray as xr
import numpy as np
from dask_image.ndmorph import binary_erosion, binary_dilation
from skimage.morphology import footprints

SCL_LEGEND = {
    'no_data': 0,
    'saturated_or_defective': 1,
    'dark_area_pixels': 2,
    'cloud_shadows': 3,
    'vegetation': 4,
    'not_vegetated': 5,
    'water': 6,
    'unclassified': 7,
    'cloud_medium_probability': 8,
    'cloud_high_probability': 9,
    'thin_cirrus': 10,
    'snow': 11
}

SCL_MASK_VALUES = [1, 3, 8, 9, 10, 11]


@dataclass
class SCLMask:
    """
    Container for processed SCL mask timeseries DataArray.

    Attributes:
    mask
    aux

    Default SCL_MASK_VALUES = [1, 3, 8, 9, 10, 11]
    """
    mask: xr.DataArray
    aux: xr.DataArray

    def __repr__(self):
        return f'<SCLMask container - mask.shape: {self.mask.shape}>'

    def clip(self, bounds):
        new_mask = self.mask.rio.clip(*bounds)
        new_aux = self.aux.rio.clip(*bounds)
        return self.__class__(new_mask, new_aux)


def preprocess_scl(scl_data,
                   mask_values=None,
                   erode_r=None,
                   dilate_r=None,
                   max_invalid_ratio=None):
    """
    From a timeseries (t, y, x) dataarray returns a binary mask False for the
    given mask_values and True elsewhere (valid pixels).

    SCL_LEGEND = {
        'no_data': 0,
        'saturated_or_defective': 1,
        'dark_area_pixels': 2,
        'cloud_shadows': 3,
        'vegetation': 4,
        'not_vegetated': 5,
        'water': 6,
        'unclassified': 7,
        'cloud_medium_probability': 8,
        'cloud_high_probability': 9,
        'thin_cirrus': 10,
        'snow': 11
    }

    Parameters:
    -----------
    slc_data: 3D array
        Input array for computing the mask

    mask_values: list
        values to set to False in the mask

    erode_r : int
        Radius for eroding disk on the mask

    dilate_r : int
        Radius for dilating disk on the mask

    max_invalid_ratio : float
        Will set mask values to True, when they have an
        invalid_ratio > max_invalid_ratio

    Returns:
    --------
    mask : 4D DataArray (time, band, y, x)
        mask True for valid pixels, False for invalid, 1 band named SCL

    aux : 4D DataArray (time, band, y, x)
        singleton time dimension (to be consistent with satio ts)
        and 7 bands:

        l2a_obs : number of valid observations (different from 0 in scl_data)
        scl_invalid_before : ratio of invalid obs before morphological operations
        scl_invalid_after : ratio of invalid obs after morphological operations
        scl_snow_cover : ratio of snow obs
        scl_water_cover : ratio of water obs
        scl_veg_cover : ratio of veg obs
        scl_notveg_cover : ratio of notveg obs
    """
    scl_data = scl_data.sel(band='SCL')

    mask_values = SCL_MASK_VALUES
    mask = da.isin(scl_data, mask_values)
    snow = scl_data == SCL_LEGEND['snow']
    water = scl_data == SCL_LEGEND['water']
    veg = scl_data == SCL_LEGEND['vegetation']
    notveg = scl_data == SCL_LEGEND['not_vegetated']

    ts_obs = scl_data != 0
    obs = ts_obs.sum(axis=0).astype(np.float32)

    ma_mask = (mask & ts_obs)
    invalid_before = ma_mask.sum(axis=0) / obs

    cover_snow = (snow & ts_obs).sum(axis=0) / obs
    cover_water = (water & ts_obs).sum(axis=0) / obs
    cover_veg = (veg & ts_obs).sum(axis=0) / obs
    cover_notveg = (notveg & ts_obs).sum(axis=0) / obs

    if (erode_r is not None) | (erode_r > 0):
        e = footprints.disk(erode_r)
        mask = da.stack([binary_erosion(m, e) for m in mask])

    if (dilate_r is not None) | (dilate_r > 0):
        d = footprints.disk(dilate_r)
        mask = da.stack([binary_dilation(m, d) for m in mask])

    ma_mask = (mask & ts_obs)
    invalid_after = ma_mask.sum(axis=0) / obs

    # invert values to have True for valid pixels and False for clouds
    mask = ~mask

    if max_invalid_ratio is not None:
        max_invalid_mask = invalid_after > max_invalid_ratio
        mask = mask | da.broadcast_to(max_invalid_mask, mask.shape)

    mask = scl_data.copy(data=mask)

    mask = mask.assign_coords(band='SCL')
    mask = mask.expand_dims('band', axis=1)

    aux_names = ['l2a_obs', 'scl_invalid_before',
                 'scl_invalid_after', 'scl_snow_cover',
                 'scl_water_cover', 'scl_veg_cover',
                 'scl_notveg_cover']

    aux = da.concatenate([obs,
                          invalid_before,
                          invalid_after,
                          cover_snow,
                          cover_water,
                          cover_veg,
                          cover_notveg], axis=0).astype(np.float32)
    print(aux.shape)

    scl_aux = xr.DataArray(aux,
                           dims=('time', 'band', 'y', 'x'),
                           coords={'time': [mask.time.values[0]],
                                   'band': aux_names,
                                   'y': mask.y,
                                   'x': mask.x},
                           attrs=mask.attrs)

    return SCLMask(mask, scl_aux)
