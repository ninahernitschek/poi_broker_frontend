from datetime import date, datetime

from sqlalchemy import inspect
import re
import json
import logging

logger = logging.getLogger(__name__)

# Helper for converting SQLAlchemy objects to dictionaries
def object_as_dict(obj) -> dict:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}

def result_to_dict(query_results)  -> list[dict]:
    def to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}    
    return [to_dict(result) for result in query_results]

# Helper function for safe serialization
def safe_serialize(obj):
    """Safely serialize a dictionary to JSON."""
    try:
        return json.dumps(obj)
    except TypeError as e:
        logger.warning("JSON serialization fallback used: %s", e)
        return json.dumps(serialize_fallback(obj))
   
def serialize_fallback(obj):
    """Fallback handler to make the object serializable by converting binary data to string."""
    if isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    elif isinstance(obj, dict):
        return {k: serialize_fallback(v) for k, v in obj.items()}  # Keep dict as-is and process its values
    elif isinstance(obj, list):
        return [serialize_fallback(v) for v in obj]  # Keep list as-is and process its elements
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    else:
        return obj  # Return other types as-is
    