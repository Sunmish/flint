"""This contains common unitilities to enable components of the prefect imaging flowws.
The majority of the items here are the task decorated functions. Effort should be made
to avoid putting in too many items that are not going to be directly used by prefect
imaging flows.
"""

from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, TypeVar, Union

import pandas as pd
from prefect import task, unmapped
from prefect.artifacts import create_table_artifact

from flint.calibrate.aocalibrate import (
    ApplySolutions,
    CalibrateCommand,
    create_apply_solutions_cmd,
    select_aosolution_for_ms,
)
from flint.coadd.linmos import LinmosCommand, linmos_images
from flint.convol import BeamShape, convolve_images, get_common_beam
from flint.flagging import flag_ms_aoflagger
from flint.imager.wsclean import WSCleanCommand, wsclean_imager
from flint.logging import logger
from flint.masking import (
    create_snr_mask_from_fits,
    create_snr_mask_wbutter_from_fits,
    extract_beam_mask_from_mosaic,
)
from flint.ms import MS, preprocess_askap_ms, split_by_field
from flint.naming import FITSMaskNames, processed_ms_format
from flint.options import FieldOptions
from flint.prefect.common.utils import upload_image_as_artifact
from flint.selfcal.casa import gaincal_applycal_ms
from flint.source_finding.aegean import AegeanOutputs, run_bane_and_aegean
from flint.utils import zip_folder
from flint.validation import (
    ValidationTables,
    XMatchTables,
    create_validation_plot,
    create_validation_tables,
)

# These are simple task wrapped functions and require no other modification
task_preprocess_askap_ms = task(preprocess_askap_ms)
task_split_by_field = task(split_by_field)
task_select_solution_for_ms = task(select_aosolution_for_ms)
task_create_apply_solutions_cmd = task(create_apply_solutions_cmd)


# Tasks below are extracting componented from earlier stages, or are
# otherwise doing something important

FlagMS = TypeVar("FlagMS", MS, ApplySolutions)


@task
def task_flag_ms_aoflagger(ms: FlagMS, container: Path, rounds: int = 1) -> FlagMS:
    extracted_ms = ms.ms if isinstance(ms, ApplySolutions) else ms

    extracted_ms = flag_ms_aoflagger(
        ms=extracted_ms, container=container, rounds=rounds
    )

    return ms


@task
def task_bandpass_create_apply_solutions_cmd(
    ms: MS, calibrate_cmd: CalibrateCommand, container: Path
) -> ApplySolutions:
    """Apply a ao-calibrate solutions file to the subject measurement set.

    Args:
        ms (MS): The measurement set that will have the solutions file applied
        calibrate_cmd (CalibrateCommand): A resulting ao-calibrate command and meta-data item
        container (Path): The container that can apply the ao-calibrate style solutions file to the measurement set

    Returns:
        ApplySolutions: The resulting apply solutions command and meta-data
    """
    return create_apply_solutions_cmd(
        ms=ms, solutions_file=calibrate_cmd.solution_path, container=container
    )


@task
def task_extract_solution_path(calibrate_cmd: CalibrateCommand) -> Path:
    """Extract the solution path from a calibrate command. This is often required when
    interacting with a ``PrefectFuture`` wrapped ``CalibrateCommand`` result.

    Args:
        calibrate_cmd (CalibrateCommand): The subject calibrate command the solution file will be extracted from

    Returns:
        Path: Path to the solution file
    """
    # TODO: What is actually using this task? Is it better to just pass through a
    # CalibrateCommand?
    # TODO: Use the get attribute task enabled function
    return calibrate_cmd.solution_path


@task
def task_run_bane_and_aegean(
    image: Union[WSCleanCommand, LinmosCommand], aegean_container: Path
) -> AegeanOutputs:
    """Run BANE and Aegean against a FITS image

    Args:
        image (Union[WSCleanCommand, LinmosCommand]): The image that will be searched
        aegean_container (Path): Path to a singularity container containing BANE and aegean

    Raises:
        ValueError: Raised when ``iamge`` is not a supported type

    Returns:
        AegeanOutputs: Output BANE and aegean products, including the RMS and BKG images
    """
    if isinstance(image, WSCleanCommand):
        assert image.imageset is not None, "Image set attribute unset. "
        image_paths = image.imageset.image

        logger.info(f"Have extracted image: {image_paths}")

        # For the moment, will only source find on an MFS image
        image_paths = [image for image in image_paths if "-MFS-" in str(image)]
        assert (
            len(image_paths) == 1
        ), "More than one image found after filter for MFS only images. "
        # Get out the only path in the list.
        image_path = image_paths[0]
    elif isinstance(image, LinmosCommand):
        logger.info("Will be running aegean on a linmos image")

        image_path = image.image_fits
        assert image_path.exists(), f"Image path {image_path} does not exist"
    else:
        raise ValueError(f"Unexpected type, have received {type(image)} for {image=}. ")

    aegean_outputs = run_bane_and_aegean(
        image=image_path, aegean_container=aegean_container
    )

    return aegean_outputs


@task
def task_zip_ms(in_item: WSCleanCommand) -> Path:
    """Zip a measurement set

    Args:
        in_item (WSCleanCommand): The inpute item with a ``.ms`` attribute of type ``MS``.

    Returns:
        Path: Output path of the zipped measurement set
    """
    # TODO: This typing needs to be expanded
    ms = in_item.ms

    zipped_ms = zip_folder(in_path=ms.path)

    return zipped_ms


@task
def task_gaincal_applycal_ms(
    wsclean_cmd: WSCleanCommand,
    round: int,
    update_gain_cal_options: Optional[Dict[str, Any]] = None,
    archive_input_ms: bool = False,
) -> MS:
    """Perform self-calibration using CASA gaincal and applycal.

    Args:
        wsclean_cmd (WSCleanCommand): A resulting wsclean output. This is used purely to extract the ``.ms`` attribute.
        round (int): Counter indication which self-calibration round is being performed. A name is included based on this.
        update_gain_cal_options (Optional[Dict[str, Any]], optional): Options used to overwrite the default ``gaincal`` options. Defaults to None.
        archive_input_ms (bool, optional): If True the input measurement set is zipped. Defaults to False.

    Raises:
        ValueError: Raised when a ``.ms`` attribute can not be obtained

    Returns:
        MS: Self-calibrated measurement set
    """
    # TODO: Need to do a better type system to include the .ms
    # TODO: This needs to be expanded to handle multiple MS
    ms = wsclean_cmd.ms

    if not isinstance(ms, MS):
        raise ValueError(
            f"Unsupported {type(ms)=} {ms=}. Likely multiple MS instances? This is not yet supported. "
        )

    return gaincal_applycal_ms(
        ms=ms,
        round=round,
        update_gain_cal_options=update_gain_cal_options,
        archive_input_ms=archive_input_ms,
    )


@task
def task_wsclean_imager(
    in_ms: Union[ApplySolutions, MS],
    wsclean_container: Path,
    update_wsclean_options: Optional[Dict[str, Any]] = None,
    fits_mask: Optional[FITSMaskNames] = None,
) -> WSCleanCommand:
    """Run the wsclean imager against an input measurement set

    Args:
        in_ms (Union[ApplySolutions, MS]): The measurement set that will be imaged
        wsclean_container (Path): Path to a singularity container with wsclean packages
        update_wsclean_options (Optional[Dict[str, Any]], optional): Options to update from the default wsclean options. Defaults to None.
        fits_mask (Optional[FITSMaskNames], optional): A path to a clean guard mask. Defaults to None.

    Returns:
        WSCleanCommand: A resulting wsclean command and resulting meta-data
    """
    ms = in_ms if isinstance(in_ms, MS) else in_ms.ms

    update_wsclean_options = (
        {} if update_wsclean_options is None else update_wsclean_options
    )

    if fits_mask:
        update_wsclean_options["fits_mask"] = fits_mask.mask_fits

    logger.info(f"wsclean inager {ms=}")
    return wsclean_imager(
        ms=ms,
        wsclean_container=wsclean_container,
        update_wsclean_options=update_wsclean_options,
    )


@task
def task_get_common_beam(
    wsclean_cmds: Collection[WSCleanCommand], cutoff: float = 25
) -> BeamShape:
    """Compute a common beam size that all input images will be convoled to.

    Args:
        wsclean_cmds (Collection[WSCleanCommand]): Input images whose restoring beam properties will be considered
        cutoff (float, optional): Major axis larger than this valur, in arcseconds, will be ignored. Defaults to 25.

    Returns:
        BeamShape: The final convolving beam size to be used
    """
    images_to_consider: List[Path] = []

    # TODO: This should support other image types
    for wsclean_cmd in wsclean_cmds:
        if wsclean_cmd.imageset is None:
            logger.warn(f"No imageset fo {wsclean_cmd.ms} found. Has imager finished?")
            continue
        images_to_consider.extend(wsclean_cmd.imageset.image)

    logger.info(
        f"Considering {len(images_to_consider)} images across {len(wsclean_cmds)} outputs. "
    )

    beam_shape = get_common_beam(image_paths=images_to_consider, cutoff=cutoff)

    return beam_shape


@task
def task_convolve_image(
    wsclean_cmd: WSCleanCommand,
    beam_shape: BeamShape,
    cutoff: float = 60,
    mode: str = "image",
) -> Collection[Path]:
    """Convolve images to a specified resolution

    Args:
        wsclean_cmd (WSCleanCommand): Collection of output images from wsclean that will be convolved
        beam_shape (BeamShape): The shape images will be convolved to
        cutoff (float, optional): Maximum major beam axis an image is allowed to have before it will not be convolved. Defaults to 60.

    Returns:
        Collection[Path]: Path to the output images that have been convolved.
    """
    assert (
        wsclean_cmd.imageset is not None
    ), f"{wsclean_cmd.ms} has no attached imageset."

    supported_modes = ("image", "residual")
    assert (
        mode in supported_modes
    ), f"{mode=} is not supported. Known modes are {supported_modes}"

    logger.info(f"Extracting {mode}")
    image_paths: Collection[Path] = (
        wsclean_cmd.imageset.image if mode == "image" else wsclean_cmd.imageset.residual
    )

    # It is possible depending on how aggressively cleaning image products are deleted that these
    # some cleaning products (e.g. residuals) do not exist. There are a number of ways one could consider
    # handling this. The pirate in me feels like less is more, so an error will be enough. Keeping
    # things simple and avoiding the problem is probably the better way of dealing with this
    # situation. In time this would mean that we inspect and handle conflicting pipeline options.
    assert (
        image_paths is not None
    ), f"{image_paths=} for {mode=} and {wsclean_cmd.imageset=}"

    logger.info(f"Will convolve {image_paths}")

    # experience has shown that astropy units do not always work correctly
    # in a multiprocessing / dask environment. The unit registery does not
    # seem to serialise correctly, and we can get weird arcsecond is not
    # compatiable with arcsecond type errors. Import here in an attempt
    # to minimise
    import astropy.units as u
    from astropy.io import fits
    from radio_beam import Beam

    # Print the beams out here for logging
    for image_path in image_paths:
        image_beam = Beam.from_fits_header(fits.getheader(str(image_path)))
        logger.info(
            f"{str(image_path.name)}: {image_beam.major.to(u.arcsecond)} {image_beam.minor.to(u.arcsecond)}  {image_beam.pa}"
        )

    return convolve_images(
        image_paths=image_paths, beam_shape=beam_shape, cutoff=cutoff
    )


@task
def task_linmos_images(
    images: Collection[Collection[Path]],
    container: Path,
    filter: str = "-MFS-",
    field_name: Optional[str] = None,
    suffix_str: str = "noselfcal",
    holofile: Optional[Path] = None,
    sbid: Optional[int] = None,
    parset_output_path: Optional[str] = None,
    cutoff: float = 0.05,
) -> LinmosCommand:
    """Run the yandasoft linmos task against a set of input images

    Args:
        images (Collection[Collection[Path]]): Images that will be co-added together
        container (Path): Path to singularity container that contains yandasoft
        filter (str, optional): Filter to extract the images that will be extracted from the set of input images. These will be co-added. Defaults to "-MFS-".
        field_name (Optional[str], optional): Name of the field, which is included in the output images created. Defaults to None.
        suffix_str (str, optional): Additional string added to the prefix of the output linmos image products. Defaults to "noselfcal".
        holofile (Optional[Path], optional): The FITS cube with the beam corrections derived from ASKAP holography. Defaults to None.
        sbid (Optional[int], optional): SBID of the data being imaged. Defaults to None.
        parset_output_path (Optional[str], optional): Location to write the linmos parset file to. Defaults to None.
        cutoff (float, optional): The primary beam attenuation cutoff supplied to linmos when coadding. Defaults to 0.05.

    Returns:
        LinmosCommand: The linmos command and associated meta-data
    """
    # TODO: Need to flatten images
    # TODO: Need a better way of getting field names

    all_images = [img for beam_images in images for img in beam_images]
    logger.info(f"Number of images to examine {len(all_images)}")

    filter_images = [img for img in all_images if filter in str(img)]
    logger.info(f"Number of filtered images to linmos: {len(filter_images)}")

    candidate_image = filter_images[0]
    candidate_image_fields = processed_ms_format(in_name=candidate_image)

    if field_name is None:
        field_name = candidate_image_fields.field
        logger.info(f"Extracted {field_name=} from {candidate_image=}")

    if sbid is None:
        sbid = candidate_image_fields.sbid
        logger.info(f"Extracted {sbid=} from {candidate_image=}")

    base_name = f"SB{sbid}.{field_name}.{suffix_str}"

    out_dir = Path(filter_images[0].parent)
    out_name = out_dir / base_name
    logger.info(f"Base output image name will be: {out_name}")

    if parset_output_path is None:
        parset_output_path = f"{out_name.name}_parset.txt"

    parset_output_path = out_dir / Path(parset_output_path)
    logger.info(f"Parsert output path is {parset_output_path}")

    linmos_cmd = linmos_images(
        images=filter_images,
        parset_output_path=Path(parset_output_path),
        image_output_name=str(out_name),
        container=container,
        holofile=holofile,
        cutoff=cutoff,
    )

    return linmos_cmd


def _convolve_linmos_residuals(
    wsclean_cmds: Collection[WSCleanCommand],
    beam_shape: BeamShape,
    field_options: FieldOptions,
    linmos_suffix_str: str,
    cutoff: float = 0.05,
) -> LinmosCommand:
    """An internal function that launches the convolution to a common resolution
    and subsequent linmos of the wsclean residual images.

    Args:
        wsclean_cmds (Collection[WSCleanCommand]): Collection of wsclean imaging results, with residual images described in the attached ``ImageSet``
        beam_shape (BeamShape): The beam shape that residual images will be convolved to
        field_options (FieldOptions): Options related to the processing of the field
        linmos_suffix_str (str): The suffix string passed to the linmos parset name
        cutoff (float, optional): The primary beam attenuation cutoff supplied to linmos when coadding. Defaults to 0.05.

    Returns:
        LinmosCommand: Resulting linmos command parset
    """
    residual_conv_images = task_convolve_image.map(
        wsclean_cmd=wsclean_cmds,
        beam_shape=unmapped(beam_shape),
        cutoff=150.0,
        mode="residual",
    )
    parset = task_linmos_images.submit(
        images=residual_conv_images,
        container=field_options.yandasoft_container,
        suffix_str=linmos_suffix_str,
        holofile=field_options.holofile,
        cutoff=cutoff,
    )

    return parset


@task
def task_create_linmos_mask_model(
    linmos_parset: LinmosCommand,
    image_products: AegeanOutputs,
    min_snr: Optional[float] = 3.5,
) -> FITSMaskNames:
    """Create a mask from a linmos image, with the intention of providing it as a clean mask
    to an appropriate imager. This is derived using a simple signal to noise cut.

    Args:
        linmos_parset (LinmosCommand): Linmos command and associated meta-data
        image_products (AegeanOutputs): Images of the RMS and BKG
        min_snr (float, optional): The minimum S/N a pixel should be for it to be included in the clean mask.

    Raises:
        ValueError: Raised when ``image_products`` are not known

    Returns:
        FITSMaskNames: Clean mask where all pixels below a S/N are masked
    """
    if isinstance(image_products, AegeanOutputs):
        linmos_image = linmos_parset.image_fits
        linmos_rms = image_products.rms
        linmos_bkg = image_products.bkg
    else:
        raise ValueError("Unsupported bkg/rms mode. ")

    logger.info(f"Creating a clean mask for {linmos_image=}")
    logger.info(f"Using {linmos_rms=}")
    logger.info(f"Using {linmos_bkg=}")

    linmos_mask_names = create_snr_mask_from_fits(
        fits_image_path=linmos_image,
        fits_bkg_path=linmos_bkg,
        fits_rms_path=linmos_rms,
        create_signal_fits=True,
        min_snr=min_snr,
    )

    logger.info(f"Created {linmos_mask_names.mask_fits}")

    return linmos_mask_names


@task
def task_create_linmos_mask_wbutter_model(
    linmos_parset: LinmosCommand,
    image_products: AegeanOutputs,
    min_snr: Optional[float] = 5,
) -> FITSMaskNames:
    """Create a mask from a linmos image, with the intention of providing it as a clean mask
    to an appropriate imager. This is derived using a simple signal to noise cut.

    This will use a butterworth filter to first smooth the image before island thresdholds
    are created. To account for the smooth a binary erosion operation is applied to the
    resulting mask.

    Args:
        linmos_parset (LinmosCommand): Linmos command and associated meta-data
        image_products (AegeanOutputs): Images of the RMS and BKG
        min_snr (float, optional): The minimum S/N a pixel should be for it to be included in the clean mask. Defaults to 5.

    Raises:
        ValueError: Raised when ``image_products`` are not known

    Returns:
        FITSMaskNames: Clean mask where all pixels below a S/N are masked
    """
    if isinstance(image_products, AegeanOutputs):
        linmos_image = linmos_parset.image_fits
        linmos_rms = image_products.rms
        linmos_bkg = image_products.bkg
    else:
        raise ValueError("Unsupported bkg/rms mode. ")

    logger.info(f"Creating a clean mask for {linmos_image=}")
    logger.info(f"Using {linmos_rms=}")
    logger.info(f"Using {linmos_bkg=}")

    linmos_mask_names = create_snr_mask_wbutter_from_fits(
        fits_image_path=linmos_image,
        fits_bkg_path=linmos_bkg,
        fits_rms_path=linmos_rms,
        create_signal_fits=True,
        min_snr=min_snr,
        connectivity_shape=(2, 2),
    )

    logger.info(f"Created {linmos_mask_names.mask_fits}")

    return linmos_mask_names


@task
def task_extract_beam_mask_image(
    linmos_mask_names: FITSMaskNames, wsclean_cmd: WSCleanCommand
) -> FITSMaskNames:
    """Extract a clean mask for a beam from a larger clean mask (e.g. derived from a field)

    Args:
        linmos_mask_names (FITSMaskNames): Mask that will be drawn from to form a smaller clean mask (e.g. for a beam)
        wsclean_cmd (WSCleanCommand): Wsclean command and meta-data. This is used to draw from the WCS to create an appropraite pixel-to-pixel mask

    Returns:
        FITSMaskNames: Clean mask for a image
    """
    # All images made by wsclean will have the same WCS
    beam_image = wsclean_cmd.imageset.image[0]
    beam_mask_names = extract_beam_mask_from_mosaic(
        fits_beam_image_path=beam_image, fits_mosaic_mask_names=linmos_mask_names
    )

    return beam_mask_names


@task
def task_create_validation_plot(
    processed_mss: List[MS],
    aegean_outputs: AegeanOutputs,
    reference_catalogue_directory: Path,
    upload_artifact: bool = True,
) -> Path:
    """Create a multi-panel figure highlighting the RMS, flux scale and astrometry of a field

    Args:
        aegean_outputs (AegeanOutputs): Output aegean products
        reference_catalogue_directory (Path): Directory containing NVSS, SUMSS and ICRS reference catalogues. These catalogues are reconginised internally and have expected names.
        upload_artifact (bool, optional): If True the validation plot will be uploaded to the prefect service as an artifact. Defaults to True.

    Returns:
        Path: Path to the output figure created
    """
    output_path = aegean_outputs.comp.parent

    logger.info(f"Will create validation plot in {output_path=}")

    processed_mss = [
        ms.ms if isinstance(ms, ApplySolutions) else ms for ms in processed_mss
    ]

    plot_path = create_validation_plot(
        processed_ms_paths=[ms.path for ms in processed_mss],
        rms_image_path=aegean_outputs.rms,
        source_catalogue_path=aegean_outputs.comp,
        output_path=output_path,
        reference_catalogue_directory=reference_catalogue_directory,
    )

    if upload_artifact:
        upload_image_as_artifact(
            image_path=plot_path, description=f"Validation plot {str(plot_path)}"
        )

    return plot_path


@task
def task_create_validation_tables(
    processed_mss: List[MS],
    aegean_outputs: AegeanOutputs,
    reference_catalogue_directory: Path,
    upload_artifacts: bool = True,
) -> ValidationTables:
    """Create a set of validation tables that can be used to assess the
    correctness of an image and associated source catalogue.

    Args:
        processed_ms_paths (List[Path]): The processed MS files that were used to create the source catalogue
        rms_image_path (Path): The RMS fits image the source catalogue was constructed against.
        source_catalogue_path (Path): The source catalogue.
        output_path (Path): The output path of the figure to create
        reference_catalogue_directory (Path): The directory that contains the reference catalogues installed

    Returns:
        ValidationTables: The tables that were created
    """
    output_path = aegean_outputs.comp.parent

    processed_mss = [
        ms.ms if isinstance(ms, ApplySolutions) else ms for ms in processed_mss
    ]

    logger.info(f"Will create validation tables in {output_path=}")
    validation_tables = create_validation_tables(
        processed_ms_paths=[ms.path for ms in processed_mss],
        rms_image_path=aegean_outputs.rms,
        source_catalogue_path=aegean_outputs.comp,
        output_path=output_path,
        reference_catalogue_directory=reference_catalogue_directory,
    )

    if upload_artifacts:
        for table in validation_tables:
            if isinstance(table, Path):
                df = pd.read_csv(table)
                df_dict = df.to_dict("records")
                create_table_artifact(
                    table=df_dict,
                    description=f"{table.stem}",
                )
            elif isinstance(table, XMatchTables):
                for subtable in table:
                    if subtable is None:
                        continue
                    if not isinstance(subtable, Path):
                        continue
                    df = pd.read_csv(subtable)
                    df_dict = df.to_dict("records")
                    create_table_artifact(
                        table=df_dict,
                        description=f"{subtable.stem}",
                    )

    return validation_tables
