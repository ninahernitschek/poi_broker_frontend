"""Feature column definitions for ZTF data."""

# All queryable float columns from the Ztf model
FEATURE_COLUMNS = {
    # Alert metadata
    'brightest_alert_magnitude_ztf': 'brightest_alert_magnitude_ztf',
       
    # Magnitude features - r-band
    'feature_amplitude_magn_r': 'feature_amplitude_magn_r',
    'feature_anderson_darling_normal_magn_r': 'feature_anderson_darling_normal_magn_r',
    'feature_beyond_1_std_magn_r': 'feature_beyond_1_std_magn_r',
    'feature_beyond_2_std_magn_r': 'feature_beyond_2_std_magn_r',
    'feature_cusum_magn_r': 'feature_cusum_magn_r',
    'feature_eta_e_magn_r': 'feature_eta_e_magn_r',
    'feature_inter_percentile_range_2_magn_r': 'feature_inter_percentile_range_2_magn_r',
    'feature_inter_percentile_range_10_magn_r': 'feature_inter_percentile_range_10_magn_r',
    'feature_inter_percentile_range_25_magn_r': 'feature_inter_percentile_range_25_magn_r',
    'feature_kurtosis_magn_r': 'feature_kurtosis_magn_r',
    'feature_linear_fit_slope_magn_r': 'feature_linear_fit_slope_magn_r',
    'feature_linear_fit_slope_sigma_magn_r': 'feature_linear_fit_slope_sigma_magn_r',
    'feature_linear_fit_reduced_chi2_magn_r': 'feature_linear_fit_reduced_chi2_magn_r',
    'feature_linear_trend_magn_r': 'feature_linear_trend_magn_r',
    'feature_linear_trend_sigma_magn_r': 'feature_linear_trend_sigma_magn_r',
    'feature_magnitude_percentage_ratio_40_5_magn_r': 'feature_magnitude_percentage_ratio_40_5_magn_r',
    'feature_magnitude_percentage_ratio_20_5_magn_r': 'feature_magnitude_percentage_ratio_20_5_magn_r',
    'feature_maximum_slope_magn_r': 'feature_maximum_slope_magn_r',
    'feature_mean_magn_r': 'feature_mean_magn_r',
    'feature_median_absolute_deviation_magn_r': 'feature_median_absolute_deviation_magn_r',
    'feature_percent_amplitude_magn_r': 'feature_percent_amplitude_magn_r',
    'feature_percent_difference_magnitude_percentile_5_magn_r': 'feature_percent_difference_magnitude_percentile_5_magn_r',
    'feature_percent_difference_magnitude_percentile_10_magn_r': 'feature_percent_difference_magnitude_percentile_10_magn_r',
    'feature_median_buffer_range_percentage_10_magn_r': 'feature_median_buffer_range_percentage_10_magn_r',
    'feature_median_buffer_range_percentage_20_magn_r': 'feature_median_buffer_range_percentage_20_magn_r',
    'feature_period_0_magn_r': 'feature_period_0_magn_r',
    'feature_period_s_to_n_0_magn_r': 'feature_period_s_to_n_0_magn_r',
    'feature_period_1_magn_r': 'feature_period_1_magn_r',
    'feature_period_s_to_n_1_magn_r': 'feature_period_s_to_n_1_magn_r',
    'feature_period_2_magn_r': 'feature_period_2_magn_r',
    'feature_period_s_to_n_2_magn_r': 'feature_period_s_to_n_2_magn_r',
    'feature_period_3_magn_r': 'feature_period_3_magn_r',
    'feature_period_s_to_n_3_magn_r': 'feature_period_s_to_n_3_magn_r',
    'feature_period_4_magn_r': 'feature_period_4_magn_r',
    'feature_period_s_to_n_4_magn_r': 'feature_period_s_to_n_4_magn_r',
    'feature_periodogram_amplitude_magn_r': 'feature_periodogram_amplitude_magn_r',
    'feature_periodogram_beyond_2_std_magn_r': 'feature_periodogram_beyond_2_std_magn_r',
    'feature_periodogram_beyond_3_std_magn_r': 'feature_periodogram_beyond_3_std_magn_r',
    'feature_periodogram_standard_deviation_magn_r': 'feature_periodogram_standard_deviation_magn_r',
    'feature_chi2_magn_r': 'feature_chi2_magn_r',
    'feature_skew_magn_r': 'feature_skew_magn_r',
    'feature_standard_deviation_magn_r': 'feature_standard_deviation_magn_r',
    'feature_stetson_k_magn_r': 'feature_stetson_k_magn_r',
    'feature_weighted_mean_magn_r': 'feature_weighted_mean_magn_r',
    # Flux features - r-band
    'feature_anderson_darling_normal_flux_r': 'feature_anderson_darling_normal_flux_r',
    'feature_cusum_flux_r': 'feature_cusum_flux_r',
    'feature_eta_e_flux_r': 'feature_eta_e_flux_r',
    'feature_excess_variance_flux_r': 'feature_excess_variance_flux_r',
    'feature_kurtosis_flux_r': 'feature_kurtosis_flux_r',
    'feature_mean_variance_flux_r': 'feature_mean_variance_flux_r',
    'feature_chi2_flux_r': 'feature_chi2_flux_r',
    'feature_skew_flux_r': 'feature_skew_flux_r',
    'feature_stetson_k_flux_r': 'feature_stetson_k_flux_r',
    # Magnitude features - g-band
    'feature_amplitude_magn_g': 'feature_amplitude_magn_g',
    'feature_anderson_darling_normal_magn_g': 'feature_anderson_darling_normal_magn_g',
    'feature_beyond_1_std_magn_g': 'feature_beyond_1_std_magn_g',
    'feature_beyond_2_std_magn_g': 'feature_beyond_2_std_magn_g',
    'feature_cusum_magn_g': 'feature_cusum_magn_g',
    'feature_eta_e_magn_g': 'feature_eta_e_magn_g',
    'feature_inter_percentile_range_2_magn_g': 'feature_inter_percentile_range_2_magn_g',
    'feature_inter_percentile_range_10_magn_g': 'feature_inter_percentile_range_10_magn_g',
    'feature_inter_percentile_range_25_magn_g': 'feature_inter_percentile_range_25_magn_g',
    'feature_kurtosis_magn_g': 'feature_kurtosis_magn_g',
    'feature_linear_fit_slope_magn_g': 'feature_linear_fit_slope_magn_g',
    'feature_linear_fit_slope_sigma_magn_g': 'feature_linear_fit_slope_sigma_magn_g',
    'feature_linear_fit_reduced_chi2_magn_g': 'feature_linear_fit_reduced_chi2_magn_g',
    'feature_linear_trend_magn_g': 'feature_linear_trend_magn_g',
    'feature_linear_trend_sigma_magn_g': 'feature_linear_trend_sigma_magn_g',
    'feature_magnitude_percentage_ratio_40_5_magn_g': 'feature_magnitude_percentage_ratio_40_5_magn_g',
    'feature_magnitude_percentage_ratio_20_5_magn_g': 'feature_magnitude_percentage_ratio_20_5_magn_g',
    'feature_maximum_slope_magn_g': 'feature_maximum_slope_magn_g',
    'feature_mean_magn_g': 'feature_mean_magn_g',
    'feature_median_absolute_deviation_magn_g': 'feature_median_absolute_deviation_magn_g',
    'feature_percent_amplitude_magn_g': 'feature_percent_amplitude_magn_g',
    'feature_percent_difference_magnitude_percentile_5_magn_g': 'feature_percent_difference_magnitude_percentile_5_magn_g',
    'feature_percent_difference_magnitude_percentile_10_magn_g': 'feature_percent_difference_magnitude_percentile_10_magn_g',
    'feature_median_buffer_range_percentage_10_magn_g': 'feature_median_buffer_range_percentage_10_magn_g',
    'feature_median_buffer_range_percentage_20_magn_g': 'feature_median_buffer_range_percentage_20_magn_g',
    'feature_period_0_magn_g': 'feature_period_0_magn_g',
    'feature_period_s_to_n_0_magn_g': 'feature_period_s_to_n_0_magn_g',
    'feature_period_1_magn_g': 'feature_period_1_magn_g',
    'feature_period_s_to_n_1_magn_g': 'feature_period_s_to_n_1_magn_g',
    'feature_period_2_magn_g': 'feature_period_2_magn_g',
    'feature_period_s_to_n_2_magn_g': 'feature_period_s_to_n_2_magn_g',
    'feature_period_3_magn_g': 'feature_period_3_magn_g',
    'feature_period_s_to_n_3_magn_g': 'feature_period_s_to_n_3_magn_g',
    'feature_period_4_magn_g': 'feature_period_4_magn_g',
    'feature_period_s_to_n_4_magn_g': 'feature_period_s_to_n_4_magn_g',
    'feature_periodogram_amplitude_magn_g': 'feature_periodogram_amplitude_magn_g',
    'feature_periodogram_beyond_2_std_magn_g': 'feature_periodogram_beyond_2_std_magn_g',
    'feature_periodogram_beyond_3_std_magn_g': 'feature_periodogram_beyond_3_std_magn_g',
    'feature_periodogram_standard_deviation_magn_g': 'feature_periodogram_standard_deviation_magn_g',
    'feature_chi2_magn_g': 'feature_chi2_magn_g',
    'feature_skew_magn_g': 'feature_skew_magn_g',
    'feature_standard_deviation_magn_g': 'feature_standard_deviation_magn_g',
    'feature_stetson_k_magn_g': 'feature_stetson_k_magn_g',
    'feature_weighted_mean_magn_g': 'feature_weighted_mean_magn_g',
    # Flux features - g-band
    'feature_anderson_darling_normal_flux_g': 'feature_anderson_darling_normal_flux_g',
    'feature_cusum_flux_g': 'feature_cusum_flux_g',
    'feature_eta_e_flux_g': 'feature_eta_e_flux_g',
    'feature_excess_variance_flux_g': 'feature_excess_variance_flux_g',
    'feature_kurtosis_flux_g': 'feature_kurtosis_flux_g',
    'feature_mean_variance_flux_g': 'feature_mean_variance_flux_g',
    'feature_chi2_flux_g': 'feature_chi2_flux_g',
    'feature_skew_flux_g': 'feature_skew_flux_g',
    'feature_stetson_k_flux_g': 'feature_stetson_k_flux_g',
    
    # Period meta
    'g_r_max': 'g_r_max',
    'g_r_mean': 'g_r_mean',
    'best_period': 'best_period',
    'best_period_significance': 'best_period_significance',
    'best_period_g': 'best_period_g',
    'best_period_r': 'best_period_r',
    'best_period_i': 'best_period_i',
    
    # Power rates
    'power_rate_0p5': 'power_rate_0p5',
    'power_rate_0p333': 'power_rate_0p333',
    'power_rate_0p25': 'power_rate_0p25',
    'power_rate_2': 'power_rate_2',
    'power_rate_3': 'power_rate_3',
    'power_rate_4': 'power_rate_4',
    
    # MHPS metrics - g-band
    'mhps_ratio_g': 'mhps_ratio_g',
    'mhps_low_g': 'mhps_low_g',
    'mhps_high_g': 'mhps_high_g',
    'mhps_non_zero_g': 'mhps_non_zero_g',
    'mhps_pn_flag_g': 'mhps_pn_flag_g',
    
    # MHPS metrics - R-band
    'mhps_ratio_R': 'mhps_ratio_R',
    'mhps_low_R': 'mhps_low_R',
    'mhps_high_R': 'mhps_high_R',
    'mhps_non_zero_R': 'mhps_non_zero_R',
    'mhps_pn_flag_R': 'mhps_pn_flag_R',
    
    # MHPS metrics - i-band
    'mhps_ratio_i': 'mhps_ratio_i',
    'mhps_low_i': 'mhps_low_i',
    'mhps_high_i': 'mhps_high_i',
    'mhps_non_zero_i': 'mhps_non_zero_i',
    'mhps_pn_flag_i': 'mhps_pn_flag_i',
    
    # DRW metrics - g-band
    'drw_omega_g': 'drw_omega_g',
    'drw_tau_g': 'drw_tau_g',
    
    # DRW metrics - R-band
    'drw_omega_R': 'drw_omega_R',
    'drw_tau_R': 'drw_tau_R',
    
    # DRW metrics - i-band
    'drw_omega_i': 'drw_omega_i',
    'drw_tau_i': 'drw_tau_i',
    
    # Amplitude - g-band
    'amplitude_g': 'amplitude_g',
    
    # Amplitude - R-band
    'amplitude_R': 'amplitude_R',
    
    # Amplitude - i-band
    'amplitude_i': 'amplitude_i',
    
    # Anderson-Darling - g-band
    'anderson_darling_g': 'anderson_darling_g',
    
    # Anderson-Darling - R-band
    'anderson_darling_R': 'anderson_darling_R',
    
    # Anderson-Darling - i-band
    'anderson_darling_i': 'anderson_darling_i',
    
    # Autocorrelation length - multi-band
    'autocorrelation_length_g': 'autocorrelation_length_g',
    'autocorrelation_length_R': 'autocorrelation_length_R',
    'autocorrelation_length_i': 'autocorrelation_length_i',
    
    # Beyond 1 std - multi-band
    'beyond1std_g': 'beyond1std_g',
    'beyond1std_R': 'beyond1std_R',
    'beyond1std_i': 'beyond1std_i',
    
    # CON - multi-band
    'con_g': 'con_g',
    'con_R': 'con_R',
    'con_i': 'con_i',
    
    # Eta E - multi-band
    'eta_e_g': 'eta_e_g',
    'eta_e_R': 'eta_e_R',
    'eta_e_i': 'eta_e_i',
    
    # GSkew - multi-band
    'gskew_g': 'gskew_g',
    'gskew_R': 'gskew_R',
    'gskew_i': 'gskew_i',
    
    # Max slope - multi-band
    'maxslope_g': 'maxslope_g',
    'maxslope_R': 'maxslope_R',
    'maxslope_i': 'maxslope_i',
    
    # Mean magnitude - multi-band
    'meanmag_g': 'meanmag_g',
    'meanmag_R': 'meanmag_R',
    'meanmag_i': 'meanmag_i',
    
    # Mean variance - multi-band
    'meanvariance_g': 'meanvariance_g',
    'meanvariance_R': 'meanvariance_R',
    'meanvariance_i': 'meanvariance_i',
    
    # Median absolute deviation - multi-band
    'medianabsdev_g': 'medianabsdev_g',
    'medianabsdev_R': 'medianabsdev_R',
    'medianabsdev_i': 'medianabsdev_i',
    
    # Median buffer range - multi-band
    'medianbrp_g': 'medianbrp_g',
    'medianbrp_R': 'medianbrp_R',
    'medianbrp_i': 'medianbrp_i',
    
    # Pair slope trend - multi-band
    'pairslopetrend_g': 'pairslopetrend_g',
    'pairslopetrend_R': 'pairslopetrend_R',
    'pairslopetrend_i': 'pairslopetrend_i',
    
    # Percent amplitude - multi-band
    'percent_amplitude_g': 'percent_amplitude_g',
    'percent_amplitude_R': 'percent_amplitude_R',
    'percent_amplitude_i': 'percent_amplitude_i',
    
    # Q31 - multi-band
    'q31_g': 'q31_g',
    'q31_R': 'q31_R',
    'q31_i': 'q31_i',
    
    # RCS - multi-band
    'rcs_g': 'rcs_g',
    'rcs_R': 'rcs_R',
    'rcs_i': 'rcs_i',
    
    # Skew - multi-band
    'skew_g': 'skew_g',
    'skew_R': 'skew_R',
    'skew_i': 'skew_i',
    
    # Small kurtosis - multi-band
    'smallkurtosis_g': 'smallkurtosis_g',
    'smallkurtosis_R': 'smallkurtosis_R',
    'smallkurtosis_i': 'smallkurtosis_i',
    
    # Standard deviation magnitude - multi-band
    'stdmag_g': 'stdmag_g',
    'stdmag_R': 'stdmag_R',
    'stdmag_i': 'stdmag_i',
    
    # Stetson K - multi-band
    'stetsonk_g': 'stetsonk_g',
    'stetsonk_R': 'stetsonk_R',
    'stetsonk_i': 'stetsonk_i',
    
    # P-chi - multi-band
    'p_chi_g': 'p_chi_g',
    'p_chi_R': 'p_chi_R',
    'p_chi_i': 'p_chi_i',
    
    # Excess variance - multi-band
    'ex_var_g': 'ex_var_g',
    'ex_var_R': 'ex_var_R',
    'ex_var_i': 'ex_var_i',
    
    # Integrated AR phi - multi-band
    'iar_phi_g': 'iar_phi_g',
    'iar_phi_R': 'iar_phi_R',
    'iar_phi_i': 'iar_phi_i',
    
    # Regression slope - multi-band
    'regression_slope_g': 'regression_slope_g',
    'regression_slope_R': 'regression_slope_R',
    'regression_slope_i': 'regression_slope_i',
    
    # Delta magnitude fid - multi-band
    'delta_mag_fid_g': 'delta_mag_fid_g',
    'delta_mag_fid_R': 'delta_mag_fid_R',
    'delta_mag_fid_i': 'delta_mag_fid_i',
    
    # Harmonics magnitudes 1-7 - multi-band
    'harmonics_mag_1_g': 'harmonics_mag_1_g',
    'harmonics_mag_1_R': 'harmonics_mag_1_R',
    'harmonics_mag_1_i': 'harmonics_mag_1_i',
    'harmonics_mag_2_g': 'harmonics_mag_2_g',
    'harmonics_mag_2_R': 'harmonics_mag_2_R',
    'harmonics_mag_2_i': 'harmonics_mag_2_i',
    'harmonics_mag_3_g': 'harmonics_mag_3_g',
    'harmonics_mag_3_R': 'harmonics_mag_3_R',
    'harmonics_mag_3_i': 'harmonics_mag_3_i',
    'harmonics_mag_4_g': 'harmonics_mag_4_g',
    'harmonics_mag_4_R': 'harmonics_mag_4_R',
    'harmonics_mag_4_i': 'harmonics_mag_4_i',
    'harmonics_mag_5_g': 'harmonics_mag_5_g',
    'harmonics_mag_5_R': 'harmonics_mag_5_R',
    'harmonics_mag_5_i': 'harmonics_mag_5_i',
    'harmonics_mag_6_g': 'harmonics_mag_6_g',
    'harmonics_mag_6_R': 'harmonics_mag_6_R',
    'harmonics_mag_6_i': 'harmonics_mag_6_i',
    'harmonics_mag_7_g': 'harmonics_mag_7_g',
    'harmonics_mag_7_R': 'harmonics_mag_7_R',
    'harmonics_mag_7_i': 'harmonics_mag_7_i',
    
    # Harmonics phases 1-7 - multi-band
    'harmonics_phi_1_g': 'harmonics_phi_1_g',
    'harmonics_phi_1_R': 'harmonics_phi_1_R',
    'harmonics_phi_1_i': 'harmonics_phi_1_i',
    'harmonics_phi_2_g': 'harmonics_phi_2_g',
    'harmonics_phi_2_R': 'harmonics_phi_2_R',
    'harmonics_phi_2_i': 'harmonics_phi_2_i',
    'harmonics_phi_3_g': 'harmonics_phi_3_g',
    'harmonics_phi_3_R': 'harmonics_phi_3_R',
    'harmonics_phi_3_i': 'harmonics_phi_3_i',
    'harmonics_phi_4_g': 'harmonics_phi_4_g',
    'harmonics_phi_4_R': 'harmonics_phi_4_R',
    'harmonics_phi_4_i': 'harmonics_phi_4_i',
    'harmonics_phi_5_g': 'harmonics_phi_5_g',
    'harmonics_phi_5_R': 'harmonics_phi_5_R',
    'harmonics_phi_5_i': 'harmonics_phi_5_i',
    'harmonics_phi_6_g': 'harmonics_phi_6_g',
    'harmonics_phi_6_R': 'harmonics_phi_6_R',
    'harmonics_phi_6_i': 'harmonics_phi_6_i',
    
    # Harmonics MSE - multi-band
    'harmonics_mse_g': 'harmonics_mse_g',
    'harmonics_mse_R': 'harmonics_mse_R',
    'harmonics_mse_i': 'harmonics_mse_i',
    
    # Harmonics chi per degree - multi-band
    'harmonics_chi_per_degree_g': 'harmonics_chi_per_degree_g',
    'harmonics_chi_per_degree_R': 'harmonics_chi_per_degree_R',
    'harmonics_chi_per_degree_i': 'harmonics_chi_per_degree_i',
}

# Ordered list for query operations; derived so the two allow-lists cannot drift.
FEATURE_COLUMN_LIST = list(FEATURE_COLUMNS)

# Initial feature-plot multiselect (explicit not derive from FEATURE_COLUMN_LIST).
DEFAULT_FEATURE_PLOT_COLUMNS = (
    'feature_amplitude_magn_r',
    'feature_anderson_darling_normal_magn_r',
    'feature_beyond_1_std_magn_r',
    'feature_beyond_2_std_magn_r',
    'feature_cusum_magn_r',
)


def default_feature_plot_columns():
    """
    Return a new list copy of DEFAULT_FEATURE_PLOT_COLUMNS.

    The main UI always applies these when opening a locus, so `/query_featureplot_data`
    normally receives a non-empty `features` query param. This matches that choice for
    direct API calls that omit `features` and for rendering the template default.
    """
    return list(DEFAULT_FEATURE_PLOT_COLUMNS)
