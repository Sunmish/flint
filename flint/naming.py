"""Attempts to centralise components to do with naming of pipeline files and data
products. 
"""
import re
from pathlib import Path
from typing import Union, Optional, NamedTuple, List, Any

from flint.logging import logger


class RawNameComponents(NamedTuple):
    date: str
    """Date that the data were taken, of the form YYYY-MM-DD"""
    time: str
    """Time that the data were written"""
    beam: str
    """Beam number of the data"""
    spw: Optional[str] = None
    """If multiple MS were written as the data were in a high-frequency resolution mode, which segment"""


def raw_ms_format(in_name: str) -> Union[None, RawNameComponents]:
    """The typical ASKAP measurement written to the ingest disks
    has the form:

    >>> 2022-04-14_100122_1.ms

    and in the case of a multiple beams written out (in high frequency resolution mode)

    >>> 2022-04-14_100122_1_1.ms

    This function will attempt to break it up into its main parts
    and return the mapping.

    Args:
        in_name (str): The name of a file, presumably a measurement set. The left-most part will be examined for to identify the raw ASKAP naming scheme.

    Returns:
        Union[None,Dict[str,str]]: None if the raw ASKAP measurement set naming scheme was not detected, otherwise a dictionary representing its parts.
    """

    logger.debug(f"Matching {in_name}")
    regex = re.compile(
        "^(?P<date>[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})_(?P<time>[0-9]+)_(?P<beam>[0-9]+)(_(?P<spw>[0-9]+))*"
    )
    results = regex.match(in_name)

    if results is None:
        logger.info(f"No results to {in_name} found")
        return None

    groups = results.groupdict()

    logger.info(f"Matched groups are: {groups}")

    return RawNameComponents(
        date=groups["date"], time=groups["time"], beam=groups["beam"], spw=groups["spw"]
    )


def extract_beam_from_name(name: Union[str, Path]) -> int:
    """Attempts to extract the beam number of a file name, presumably a measurement set,
    that has the form similar to the typical format of raw measurement sets freshly written
    to disk. Such a search might be useful when considering images where the beam information
    might not be attached in the FITS file (or otherwise passed around).

    Args:
        name (Union[str,Path]): The name to examine to search for the beam number.

    Raises:
        ValueError: Raised if the name was not was not successfully matched against the known format

    Returns:
        int: Beam number that extracted from the input name
    """

    name = str(name.name) if isinstance(name, Path) else name
    results = raw_ms_format(in_name=name)

    if results is None:
        raise ValueError(f"Unrecognised file name format for {name=}. ")

    return int(results.beam)


def create_ms_name(
    ms_path: Path, sbid: Optional[int] = None, field: Optional[str] = None
) -> str:
    """Create a consistent naming scheme for measurement sets. At present
    it is intented to be used for splitting fields from raw measurement
    sets, but can be expanded.

    Args:
        ms_path (Path): The measurement set being considered. A RawNameComponents will be constructed against it.
        sbid (Optional[int], optional): An explicit SBID to include in the name, otherwise one will attempted to be extracted the the ms path. If these fail the sbid is set of 00000. Defaults to None.
        field (Optional[str], optional): The field that this measurement set will contain. Defaults to None.

    Returns:
        str: The filename of the measurement set
    """

    # TODO: What to do if the MS does not work with RawMSComponents?

    ms_path = Path(ms_path).absolute()
    ms_name_list: List[Any] = []

    # Use the explicit SBID is provided, otherwise attempt
    # to extract it
    sbid_text = "SB0000"
    if sbid:
        sbid_text = f"SB{sbid}"
    else:
        try:
            sbid = get_sbid_from_path(path=ms_path)
            sbid_text = f"SB{sbid}"
        except:
            pass
    ms_name_list.append(sbid_text)

    if field:
        ms_name_list.append(field)

    components = raw_ms_format(in_name=ms_path.name)
    if components:
        ms_name_list.append(f"beam{components.beam}")
        if components.spw:
            ms_name_list.append(f"spw{components.spw}")

    ms_name_list.append("ms")
    ms_name = ".".join(ms_name_list)

    return ms_name


class AegeanNames(NamedTuple):
    """Base names that would be used in various Aegean related tasks"""

    bkg_image: Path
    """Background map computed by BANE"""
    rms_image: Path
    """RMS noise map computed by BANE"""
    comp_cat: Path
    """Component catalogue produced by the aegean source finder"""
    ds9_region: Path
    """DS9 region overlay file"""
    resid_image: Path
    """Residual map after subtracting component catalogue produced by AeRes"""


def create_aegean_names(base_output: str) -> AegeanNames:
    """Create the expected names for aegean and its tools.

    Args:
        base_output (str): The base name that aegean outputs are built from.

    Returns:
        AegeanNames: A collection of names to be produced by Aegean related tasks
    """
    base_output = str(base_output)

    return AegeanNames(
        bkg_image=Path(f"{base_output}_bkg.fits"),
        rms_image=Path(f"{base_output}_rms.fits"),
        comp_cat=Path(f"{base_output}_comp.fits"),
        ds9_region=Path(f"{base_output}_overlay.reg"),
        resid_image=Path(f"{base_output}_residual.fits"),
    )


class LinmosNames(NamedTuple):
    """Creates expected output names for the yandasoft linmos task."""

    image_fits: Path
    """Path to the final fits co-added image"""
    weight_fits: Path
    """Path to the weights fits image created when co-adding images"""


def create_linmos_names(name_prefix: str) -> LinmosNames:
    """This creates the names that would be output but the yandasoft
    linmos task. It returns the names for the linmos and weight maps
    that linmos would create. These names will have the .fits extension
    with them, but be aware that when the linmos parset if created
    these are omitted.

    Args:
        name_prefix (str): The prefix of the filename that will be used to create the linmos and weight file names.

    Returns:
        LinmosNames: Collection of expected filenames
    """
    logger.info(f"Linmos name prefix is: {name_prefix}")
    return LinmosNames(
        image_fits=Path(f"{name_prefix}_linmos.fits"),
        weight_fits=Path(f"{name_prefix}_weight.fits"),
    )


def get_sbid_from_path(path: Path) -> int:
    """Attempt to extract the SBID of a observation from a path. It is a fairly simple ruleset
    that follows the typical use cases that are actually in practise. There is no mechanism to
    get the SBID from the measurement set meta-data.

    If the path provided ends in a .ms suffix, the parent directory is assumed to be named
    the sbid. Otherwise, the name of the subject directory is. A test is made to ensure the
    sbid is made up of integers only.

    Args:
        path (Path): The path that contains the sbid to extract.

    Raises:
        ValueError: Raised when the SBID extracted from the path is not all digits

    Returns:
        int: The sbid extracted
    """
    path = Path(path)
    path_suffix = path.suffix

    logger.debug(f"Suffix of {path} is {path_suffix}")

    if path_suffix.endswith(".ms"):
        logger.debug("This is a measurement set, so sbid must be the parent directory")
        sbid = path.parent.name
    else:
        sbid = path.name

    if not sbid.isdigit():
        raise ValueError(
            f"Extracted {sbid=} from {str(path)} failed appears to be non-conforming - it is not a number! "
        )

    return int(sbid)
