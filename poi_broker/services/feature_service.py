"""Feature query service for handling ZTF feature data."""

import logging
from sqlalchemy.orm import Query
from .. import db
from ..models import Ztf
from ..constants.features import FEATURE_COLUMN_LIST, default_feature_plot_columns

logger = logging.getLogger(__name__)


def query_features_by_alert_id(alert_id):
    """
    Query all features for a given alert_id.
    
    Args:
        alert_id: The alert_id to query
    
    Returns:
        dict: Feature data as a dictionary, or None if not found
    """
    try:
        # Select only the feature columns to avoid returning unrelated fields like date_log
        selected_columns = [getattr(Ztf, col) for col in FEATURE_COLUMN_LIST]
        feature_query = db.session.query(*selected_columns)
        feature_query = feature_query.filter(Ztf.alert_id == alert_id)

        row = feature_query.first()
        if row is None:
            return None

        # Row is a SQLAlchemy Row object with keys matching FEATURE_COLUMN_LIST
        return {col: getattr(row, col) for col in FEATURE_COLUMN_LIST}
    except Exception as e:
        logger.error(f'Error querying features for alert_id {alert_id}: {str(e)}', exc_info=True)
        raise


def _resolve_feature_plot_columns(selected_features: list[str] | None) -> list[str]:
    """Return up to 10 known feature names, or the plot defaults."""
    if not selected_features:
        return default_feature_plot_columns()
    feature_list = [f for f in selected_features[:10] if f in FEATURE_COLUMN_LIST]
    if not feature_list:
        return default_feature_plot_columns()
    return feature_list


def build_feature_plot_query(
    locus_id: str, selected_features: list[str] | None = None
) -> tuple[Query, list[str]]:
    """
    Build a column-projected feature-plot query for a locus.

    Returns:
        tuple: (query, feature_list) — query selects date_alert_mjd,
            ant_mag_corrected, and the resolved feature columns.
    """
    feature_list = _resolve_feature_plot_columns(selected_features)
    columns_to_load = [Ztf.date_alert_mjd, Ztf.ant_mag_corrected] + [
        getattr(Ztf, col) for col in feature_list
    ]
    query = db.session.query(*columns_to_load).filter(Ztf.locus_id == locus_id)
    return query, feature_list


def query_feature_plot_data(locus_id, selected_features=None):
    """
    Query feature plot data for a given locus_id.
    
    Args:
        locus_id: The locus_id to query
        selected_features: List of feature column names to load (optional, defaults via default_feature_plot_columns)
    
    Returns:
        tuple: (rows, feature_list) where rows are column-projected SQLAlchemy Rows
            with date_alert_mjd, ant_mag_corrected, and the selected feature columns.
    """
    try:
        query, feature_list = build_feature_plot_query(locus_id, selected_features)
        return query.all(), feature_list
    except Exception as e:
        logger.error(f'Error querying feature plot data for locus_id {locus_id}: {str(e)}', exc_info=True)
        raise
