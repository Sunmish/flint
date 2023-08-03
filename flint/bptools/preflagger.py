"""Tooling to attempt to flag components related to bandpasses. The idea
being that poorly calibration channels in the antenna-based gains should
be removed. 

At this point there are no attempts to smooth or interpolate these flagged
components of the bandpass. 
"""
from typing import NamedTuple, Tuple, Optional
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

from flint.logging import logger


class PhaseOutlierResults(NamedTuple):
    """Results from the attempt to identify outlier complex gains in
    the bandpass solutions. This procedure is concerned with identifying
    channel-wise outliers by first unwrapping any uncorrected delay term
    in the complex_gains, fitting to the unwrapped phase components, and
    then fitting.
    """

    complex_gains: np.ndarray
    """The input gains to plot"""
    init_model_gains: np.ndarray
    """The initial model of the complex_gains"""
    fit_model_gains: np.ndarray
    """The complex gain model fit made against the unwrapped gains (i.e. complex_gains / init_model_gains)"""
    init_model_params: Tuple[float, float]
    """The initial guess (gradient, offset) model parameters to represent the phase component of the complex_gains"""
    fit_model_params: Tuple[float, float]
    """The fitted model parameters constrained against the unwrapped gains"""
    outlier_mask: np.ndarray
    """Boolean mask of equal length to complex_gain, where True represents outliers that should be flagged"""
    unwrapped_residual_mean: float
    """The mean of the residual unwrapped phases in radians"""
    unwrapped_residual_std: float
    """The std. of the residual unwrapped phases in radians"""
    flag_cut: float
    """The adopted signifance level that a outlier should be before outlier_mask is set to True"""


# TODO: Pass in parameters directly so we don't have to have an instance of PhaseOutlierResults
def plot_phase_outlier(
    phase_outlier_results: PhaseOutlierResults,
    output_path: Path,
    title: Optional[str] = None,
) -> Path:
    """Create a simple diagnostic plot highlighting how the outlier
    channels and their phases were selected.

    Args:
        phase_outlier_results (PhaseOutlierResults): Results from the outlier phase flagging method
        output_path (Path): Location to write the output plot to
        title (str, optional): Title to add to the figure. Defaults to None.

    Returns:
        Path: Path of the output image file
    """
    logger.info(f"Creating phase outlier plot, writting {str(output_path)}.")

    complex_gains = phase_outlier_results.complex_gains
    init_model_gains = phase_outlier_results.init_model_gains
    fit_model_gains = phase_outlier_results.fit_model_gains
    unwrapped_mean = phase_outlier_results.unwrapped_residual_mean
    unwrapped_std = phase_outlier_results.unwrapped_residual_std
    flag_cut = phase_outlier_results.flag_cut
    outlier_mask = phase_outlier_results.outlier_mask

    xs = np.arange(complex_gains.shape[0])

    residual_fit_gains = complex_gains / init_model_gains / fit_model_gains

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 4))

    ax1.plot(xs, np.angle(complex_gains))
    ax1.plot(
        xs[outlier_mask], np.angle(complex_gains[outlier_mask]), "bo", label="Flagged"
    )
    ax1.plot(
        xs, np.angle(init_model_gains), color="red", label="Initial Model", alpha=0.4
    )
    ax1.plot(
        xs,
        np.angle(init_model_gains * fit_model_gains),
        color="black",
        label="Fitted Model",
        alpha=0.4,
    )
    ax1.legend()
    ax1.set(xlabel="Channels", ylabel="Phase (rad)", title="Raw Data")
    ax1.grid()

    ax2.plot(xs, np.angle(residual_fit_gains), label="Residual")
    ax2.plot(
        xs,
        np.angle(complex_gains / init_model_gains),
        label="Init. Model Unwrapped",
        alpha=0.4,
    )
    ax2.plot(
        xs[outlier_mask],
        np.angle(residual_fit_gains[outlier_mask]),
        "bo",
        label="Flagged",
    )
    ax2.axhline(
        unwrapped_mean,
        color="red",
        ls="-",
    )
    ax2.axhline(
        unwrapped_mean - flag_cut * unwrapped_std,
        color="red",
        ls="--",
    )
    ax2.axhline(
        unwrapped_mean + flag_cut * unwrapped_std,
        color="red",
        ls="--",
    )

    ax2.grid()
    ax2.legend()
    ax2.set(xlabel="Channels", ylabel="Phase (rad)", title="Initial Unwrapped")

    if title:
        fig.suptitle(title)

    fig.tight_layout()

    fig.savefig(str(output_path))

    return output_path


def complex_gain_model(
    xs: np.ndarray, gradient: float, phase_offset: float
) -> np.ndarray:
    """Simulate a simple set of complex gains. No consideration made to amplitudes,
    only considering phase

    Args:
        xs (np.ndarray): Positions to evaluate model at.
        gradient (float): Gradient of the phase-slope, rate of wrapping
        phase_offset (float): Initial phasor offset of the model

    Returns:
        np.ndarray: Equal length to input xs of complex numbers representing phase-ramp
    """

    gains = np.exp(1j * 2.0 * np.pi * gradient * xs + phase_offset * 1j)
    return gains


def fit_complex_gain_model(*args):
    """A fudge to help the curve_fit along."""
    return np.angle(complex_gain_model(*args))


def flag_outlier_phase(
    complex_gains: np.ndarray,
    flag_cut: float,
    use_mad: bool = False,
    plot_title: Optional[str] = None,
    plot_path: Optional[Path] = None,
) -> PhaseOutlierResults:
    """This procedure attempts to identify channels in the bandpass solutions to
    flag but searching for gains with outlier phases. Typically, ASKAP solutions
    have a phase-slope across the band (i.e. a delay). This phase-slope first
    needs to be 'unwrapped' in order to correctly identify outliers reliably.

    Internally this function constructs an initial model of the unknown phase-slope.
    It estimates this by looking at the bulk set of gradients on a channel-to-channel
    basis, and looking at the builk distribution after removing large jumps (possibly
    RFI, possible a wrap). The initial phase offset is taken as the phase of the first
    valid gain.

    The initial model is used to unwrap the data, allowing a lest-squares fitter to
    more reliably fit. Once the fitter has been executed, the final cuts are applied
    against the unwrapped phase residuals.

    Experience shows that best results are achieved when the input complex-gains
    have been normalised against a reference antenna. There may be complex structures
    when the raw antenna phase vs frequency plots that can not be reliably fit for
    in this manner. These structures (I believe) arise from the beam-wise spectral
    sub-windows. See BPTools for a more thorough explanation.

    Args:
        complex_gains (np.ndarray): The comples-gains as a function of frequency.
        flag_cut (float): The significance a point should be before flagged as outlier
        use_mad (bool, optional): Use the median and MAD when selecting outlier. if False, use mean and std. Defaults to False.
        plot_title (str, optional): Title to add to the plot. Defaults to None.
        plot_path (Path, optional): If not None, a simple diagnostic plot will be created and written to this path. Defaults to None.

    Returns:
        PhaseOUtlierResults: Collection of results from this phase outlier flagging routine
    """

    idxs = np.arange(complex_gains.shape[0])

    # Step one: attempt to guess initial conditions of model to unwrap.
    # These calibration solutions typically have a unknown delay and phase
    # offset. For the least-squares fitter to fit the data robustly, decent
    # initial guesses are needed.
    complex_mask = np.isfinite(complex_gains)
    gain_angles = np.angle(complex_gains)
    init_phase_offset = gain_angles[complex_mask][0]

    # dividing by run is not strictly needed at the moment
    init_gradients = (gain_angles[1:] - gain_angles[:-1]) / (idxs[1:] - idxs[:-1])
    # The second half of this mask is to capture and exclude moments where the phase slop wraps.
    # The initial guess just needs to be in the ball park.
    init_gradients_mask = np.isfinite(init_gradients) & (
        np.abs(init_gradients) < np.pi / 2
    )
    init_gradient = np.median(init_gradients[init_gradients_mask])
    # TODO: Pretty sure this 2pi factor can be removed if the complex_gains_model
    # also has its 2pi removed. It feels like I am missing a something here.
    init_p0 = (init_gradient / (2.0 * np.pi), init_phase_offset)
    init_model_gains = complex_gain_model(idxs, *init_p0)

    # Now construct the initial guess model, used to unwrap the data
    unwrapped_complex_gains = complex_gains / init_model_gains

    # Since there should be a fairly decent initial unwrapped with
    # an additional additive offset to set the bulk of the phases
    # to near zero, we can pass the fitter a fairly simple guesses
    p0 = [0, 0]
    results = curve_fit(
        fit_complex_gain_model,
        idxs[complex_mask],
        np.angle(unwrapped_complex_gains)[complex_mask],
        p0,
    )

    fit_model_gains = complex_gain_model(idxs, *results[0])

    # Make the residuals
    unwrapped_residuals = np.angle(unwrapped_complex_gains / fit_model_gains)
    unwrapped_residuals_mask = np.isfinite(unwrapped_residuals)

    # Apply the final cuts to identify channels of excess phase offset, indicating
    # RFI.
    valid_residuals = unwrapped_residuals[unwrapped_residuals_mask]

    unwrapped_residual_median = np.median(valid_residuals)
    unwrapped_residual_mad = np.median(
        np.abs(valid_residuals - unwrapped_residual_median)
    )

    unwrapped_residual_mean = np.nanmean(unwrapped_residuals)
    unwrapped_residual_std = np.nanstd(unwrapped_residuals)

    if use_mad:
        m, s = unwrapped_residual_median, unwrapped_residual_mad
    else:
        m, s = unwrapped_residual_mean, unwrapped_residual_std

    final_mask = np.isfinite(unwrapped_residuals) & (
        np.abs(unwrapped_residuals) < (m + flag_cut * s)
    )

    phase_outlier_results = PhaseOutlierResults(
        complex_gains=complex_gains,
        init_model_gains=init_model_gains,
        fit_model_gains=fit_model_gains,
        init_model_params=init_p0,
        fit_model_params=results[0],
        outlier_mask=~final_mask,
        unwrapped_residual_mean=m,
        unwrapped_residual_std=s,
        flag_cut=flag_cut,
    )

    if plot_path:
        plot_phase_outlier(
            phase_outlier_results=phase_outlier_results,
            output_path=plot_path,
            title=plot_title,
        )

    return phase_outlier_results


def flags_over_threshold(
    flags: np.ndarray, thresh: float = 0.8, ant_idx: Optional[int] = None
) -> bool:
    """Given a set of flags for an antenna across frequency, consider how much is flagged, indicated
    by a value of True, and return whether it was over a threshold. The intent is to return whether
    an entire antenna should be flagged.

    Args:
        flags (np.ndarray): Array of flags to consider
        thresh (float, optional): Threshold as a fraction that has to be meet before considered bad. Defaults to 0.8.
        ant_idx (Optional[int], optional): Number of the antenna being considered. Defaults to None.

    Returns:
        bool: Whether the number of flags has reached a threshold
    """

    assert (
        0.0 <= thresh <= 1.0
    ), f"The provided {thresh=} should be a fraction between 0 to 1. "

    number_flagged = np.sum(flags)
    # Use the shape incase multi-dimensional array passed in
    total_flagged = np.prod(flags.shape)

    frac_flagged = number_flagged / total_flagged
    thresh_str = f"Total flagged: {frac_flagged:2.2f}"
    thresh_str = f"Antenna {ant_idx:02d} - {thresh_str}"

    logger.info(thresh_str)

    return frac_flagged > thresh


def flag_mean_residual_amplitude(
    complex_gains: np.ndarray, use_robust: bool = True, polynomial_order: int = 5
) -> bool:
    """Calculate the median or mean of the residual amplitudes of the complex gains
    after fitting a polynomial of order polynomial_order.

    If this median/mean is above 0.1 a value of True is returned, indicating
    that the antenna should be flagged.

    Args:
        complex_gains (np.ndarray): The set of complex gains to be considered
        use_robust (bool, optional): Whether to use robust statistics (median, MAD)  or mean/std to calculate the statistic against. Defaults to True.
        polynomical_order (int, optional): The order of the polynomical (numpy.polyfit) to use to compute the baseline. Defaults to 5.

    Returns:
        bool: Whether the data should be considered bad. True if it is bad, False if otherwise.
    """

    amplitudes = np.abs(complex_gains)
    idxs = np.arange(amplitudes.shape[0])
    mask = np.isfinite(amplitudes)

    poly_coeffs = np.polyfit(idxs[mask], amplitudes[mask], order=polynomial_order)
    poly_vals = np.polyval(poly_coeffs, idxs)

    residual = amplitudes - poly_vals
    # Although the mask above should be sufficent, trust nothing
    mask = np.isfinite(residual)

    # TODO: Consider use of an iterative clipping method
    if use_robust:
        mean = np.median(residual[mask])
        deviation = np.abs(residual) - mean
        deviation = np.median(deviation[np.isfinite(deviation)])
    else:
        mean = np.mean(residual[mask])
        deviation = np.std(residual[mask])

    bad = np.abs(mean) > 0.1 or deviation > 0.5

    return bad
