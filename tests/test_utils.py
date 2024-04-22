"""Basic tests for utility functions"""

import pytest
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.io import fits

from flint.utils import (
    estimate_skycoord_centre,
    get_packaged_resource_path,
    generate_stub_wcs_header,
    generate_strict_stub_wcs_header,
)
from flint.logging import logger


@pytest.fixture
def rms_path(tmpdir):
    rms_path = Path(
        get_packaged_resource_path(
            package="flint.data.tests",
            filename="SB39400.RACS_0635-31.beam0-MFS-subimage_rms.fits",
        )
    )

    return rms_path


def test_generate_strict_header_position():
    """Use a reference WCS to calculate the pixel position expected of a source"""

    # The following WCS are taken from SB40470, beam 17, and the header produced from wsclean
    # This beam has 3C 298 in it
    wcs_dict = dict(
        NAXIS1=8128,
        NAXIS2=8128,
        ORIGIN="WSClean",
        CTYPE1="RA---SIN",
        CRPIX1=4065,
        CRVAL1=-1.722664244157e02,
        CDELT1=-6.944444444444e-04,
        CUNIT1="deg",
        CTYPE2="DEC--SIN",
        CRPIX2=4065,
        CRVAL2=2.625771981318e00,
        CDELT2=6.944444444444e-04,
        CUNIT2="deg",
        CTYPE3="FREQ",
        CRPIX3=1,
        CRVAL3=8.874907407407e08,
        CDELT3=2.880000000000e08,
        CUNIT3="Hz",
        CTYPE4="STOKES",
        CRPIX4=1,
        CRVAL4=1.000000000000e00,
        CDELT4=1.000000000000e00,
        CUNIT4="",
    )
    center_position = SkyCoord(wcs_dict["CRVAL1"] * u.deg, wcs_dict["CRVAL2"] * u.deg)

    wcs = generate_strict_stub_wcs_header(
        position_at_image_center=center_position,
        image_shape=(
            int(wcs_dict["CRPIX1"]),
            int(wcs_dict["CRPIX2"]),
        ),
        pixel_scale=wcs_dict["CDELT2"] * u.deg,
        image_shape_is_center=True,
    )

    logger.info(f"{wcs=}")

    known_tato = SkyCoord("12:29:06 02:03:08", unit=(u.hourangle, u.deg))

    pixels = wcs.world_to_pixel(known_tato)
    logger.info(pixels)
    pixels = tuple([int(np.round(pixels[0])), int(np.round(pixels[1]))])

    assert pixels == (4724, 3238)


def test_generate_strict_wcs_header():
    """Generate an expects WCS header from known inputs"""
    image_shape = (2000, 2000)
    w = generate_strict_stub_wcs_header(
        position_at_image_center=SkyCoord(180, -30, unit=(u.deg, u.deg)),
        image_shape=image_shape,
        pixel_scale=-2.5 * u.arcsec,
    )
    assert isinstance(w, WCS)
    assert w.wcs.ctype[0] == "RA---SIN"
    assert w.wcs.ctype[1] == "DEC--SIN"

    w = generate_strict_stub_wcs_header(
        position_at_image_center=SkyCoord(180, -30, unit=(u.deg, u.deg)),
        image_shape=image_shape,
        pixel_scale="2.5arcsec",
    )
    assert isinstance(w, WCS)
    assert w.wcs.ctype[0] == "RA---SIN"
    assert w.wcs.ctype[1] == "DEC--SIN"


def test_wcs_getter():
    """Make a basic wcs object"""
    w = generate_stub_wcs_header(
        ra=180, dec=-45, image_shape=(8000, 8000), pixel_scale=0.01
    )

    assert isinstance(w, WCS)
    assert w._naxis == (8000, 8000)
    assert w.wcs.ctype[0] == "RA---SIN"
    assert w.wcs.ctype[1] == "DEC--SIN"


def test_wcs_getter_with_valueerrors(rms_path):
    """Make a basic wcs object"""
    # This one tests the pixel scale not being a quantity
    with pytest.raises(AssertionError):
        _ = generate_stub_wcs_header(ra=180, dec=-45, pixel_scale=2)

    # This one tests something being a None
    with pytest.raises(ValueError):
        _ = generate_stub_wcs_header(ra=180, dec=-45, pixel_scale="2.5arcsec")

    # This one tests a bad projection
    with pytest.raises(AssertionError):
        _ = generate_stub_wcs_header(
            ra=180, dec=-45, projection="ThisIsBad", pixel_scale="2.5arcsec"
        )

    # This one tests missing ra and dec but draws from the base
    _ = generate_stub_wcs_header(
        projection="SIN", pixel_scale="2.5arcsec", base_wcs=rms_path
    )

    # This one tests drawing everything from the base wcs
    w = generate_stub_wcs_header(base_wcs=rms_path)
    assert w._naxis == (15, 10)
    assert w.wcs.ctype[0] == "RA---SIN"
    assert w.wcs.ctype[1] == "DEC--SIN"

    w = generate_stub_wcs_header(
        ra=180, dec=-45, pixel_scale="2.5arcsec", base_wcs=rms_path
    )
    assert w._naxis == (15, 10)
    assert w.wcs.ctype[0] == "RA---SIN"
    assert w.wcs.ctype[1] == "DEC--SIN"


def test_wcs_getter_positions():
    """Make a basic wcs object"""
    # TODO: Need some proper tests here. Translate to sky positions etc
    w = generate_stub_wcs_header(
        ra=180, dec=-45, image_shape=(8000, 8000), pixel_scale=0.01
    )

    assert isinstance(w, WCS)

    w2 = generate_stub_wcs_header(
        ra=(180 * u.deg).to(u.rad),
        dec=-(45 * u.deg).to(u.rad),
        image_shape=(8000, 8000),
        pixel_scale=0.01,
    )

    assert isinstance(w, WCS)
    assert np.allclose(w.wcs.crval, w2.wcs.crval)


def test_wcs_getter_quantity():
    """Make a basic wcs object that includes different pixel types"""
    w = generate_stub_wcs_header(
        ra=180, dec=-45, image_shape=(8000, 8000), pixel_scale=0.01 * u.deg
    )
    assert isinstance(w, WCS)

    w = generate_stub_wcs_header(
        ra=180, dec=-45, image_shape=(8000, 8000), pixel_scale="2.5arcsec"
    )
    assert isinstance(w, WCS)

    w = generate_stub_wcs_header(
        ra=180 * u.deg,
        dec=-45 * u.rad,
        image_shape=(8000, 8000),
        pixel_scale="2.5arcsec",
    )
    assert isinstance(w, WCS)


def test_wcs_getter_withbase(rms_path):
    """Make a wcs object overriding the wcs from an existing fits file"""
    hdr = fits.getheader(rms_path)
    w = generate_stub_wcs_header(
        ra=180,
        dec=-45,
        image_shape=(8000, 8000),
        pixel_scale=0.01,
        base_wcs=WCS(hdr),
    )

    assert isinstance(w, WCS)

    w2 = generate_stub_wcs_header(
        ra=180,
        dec=-45,
        image_shape=(8000, 8000),
        pixel_scale=0.01,
        base_wcs=rms_path,
    )

    assert isinstance(w2, WCS)


def test_package_resource_path_folder():
    """Ensure the utils package path resource getter works"""
    dir_path = get_packaged_resource_path(package="flint.data", filename="")

    assert isinstance(dir_path, Path)
    assert dir_path.exists()


def test_package_resource_path_askap_lua():
    """Ensure the utils package path resource getter work, and check the contents of a file"""
    askap_lua = get_packaged_resource_path(
        package="flint.data.aoflagger", filename="ASKAP.lua"
    )

    assert isinstance(askap_lua, Path)
    assert askap_lua.exists()

    with open(askap_lua, "r") as open_lua:
        line = open_lua.readline()
        assert line == "--[[\n"


def test_package_resource_path_skymodel():
    """Ensure the utils package path resource getter work, and check the contents of a file"""
    askap_model = get_packaged_resource_path(
        package="flint.data.models", filename="1934-638.calibrate.txt"
    )

    assert isinstance(askap_model, Path)
    assert askap_model.exists()

    with open(askap_model, "r") as open_model:
        line = open_model.readline()
        assert (
            line
            == "Format = Name, Type, Ra, Dec, I, SpectralIndex, LogarithmicSI, ReferenceFrequency='888500000.0', MajorAxis, MinorAxis, Orientation\n"
        )


def test_estimate_skycoord_centre():
    """Estimate the centre position from a collection of sky positions"""
    ras = np.arange(-3, 3, 1) + 180.0
    decs = np.arange(-3, 3, 1) - 40.0

    sky_pos = SkyCoord(ras, decs, unit=(u.deg, u.deg))

    mean_pos = estimate_skycoord_centre(sky_positions=sky_pos)

    print(mean_pos)

    assert np.isclose(mean_pos.ra.deg, 179.54350474)
    assert np.isclose(mean_pos.dec.deg, -40.51256163)


def test_estimate_skycoord_centre_wrap():
    """Estimate the mean center sky position that wraps around 360 -> 0 degrees in ra"""
    ras = np.arange(-3, 3, 1) + 360.0 % 360
    decs = np.arange(-3, 3, 1) - 40.0

    sky_pos = SkyCoord(ras, decs, unit=(u.deg, u.deg))

    mean_pos = estimate_skycoord_centre(sky_positions=sky_pos)

    print(mean_pos)

    assert np.isclose(mean_pos.ra.deg, 359.54349533)
    assert np.isclose(mean_pos.dec.deg, -40.51255648)
