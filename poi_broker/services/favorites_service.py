"""Favorites persistence service for managing user favorites and groups."""

import logging
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from .. import db
from ..models import Favorite, FavoriteGroup

logger = logging.getLogger(__name__)

# Sentinel to distinguish "no filter specified" from an explicit None (ungrouped)
_NO_FILTER = object()


def get_favorite_status(locus_id):
    """
    Check if a locus_id is favorited by the current user.
    
    Args:
        locus_id: The locus_id to check
    
    Returns:
        bool: True if favorited, False otherwise (or if not authenticated)
    """
    if not current_user.is_authenticated:
        return False
    
    if not locus_id:
        return False
    
    fav = Favorite.query.filter_by(user_id=current_user.id, locus_id=locus_id).first()
    return fav is not None


def get_user_favorites(group_id=_NO_FILTER):
    """
    Get favorites for the current user.

    Args:
        group_id: If omitted (default), no group filter is applied.
                  If provided as an integer, returns favorites for that group.
                  If provided explicitly as None, returns only ungrouped favorites.

    Returns:
        list: List of dicts with 'id' and 'locusId'
    """
    if not current_user.is_authenticated:
        return []

    query = Favorite.query.filter_by(user_id=current_user.id)

    # If caller provided a group_id (including explicit None), filter by it.
    if group_id is not _NO_FILTER:
        query = query.filter_by(group_id=group_id)

    favs = [{'id': r.id, 'locusId': r.locus_id} for r in query.all()]
    return favs


def toggle_favorite(locus_id, fav_flag, group_id=None):
    """
    Add or remove a favorite for the current user.
    
    Args:
        locus_id: The locus_id to favorite
        fav_flag: True to add, False to remove
        group_id: Optional group to assign to (None for ungrouped). Always None when a favorite is added in the main UI, but can be used to move existing favorites between groups.
    
    Returns:
        dict: Status dict with 'status': 'ok' or error message
    """
    if not current_user.is_authenticated:
        return {'error': 'authentication required'}, 401
    
    if not locus_id:
        return {'error': 'locusId required'}, 400
    
    try:
        fav = Favorite.query.filter_by(user_id=current_user.id, locus_id=locus_id).first()
        
        if fav_flag:
            # Validate group ownership on both insert and update
            if group_id is not None:
                group = FavoriteGroup.query.filter_by(id=group_id, user_id=current_user.id).first()
                if not group:
                    return {'error': 'group not found'}, 404

            if not fav:
                fav = Favorite(user_id=current_user.id, locus_id=locus_id, group_id=group_id)
                db.session.add(fav)
            else:
                fav.group_id = group_id
            db.session.commit()
        else:
            if fav:
                db.session.delete(fav)
                db.session.commit()
        
        return {'status': 'ok'}, 200
    
    except IntegrityError:
        db.session.rollback()
        return {'error': 'The specified favorite or group cannot be toggled.'}, 409
    except Exception as e:
        logger.error(f'Error toggling favorite for locus_id {locus_id}: {str(e)}', exc_info=True)
        db.session.rollback()
        return {'error': 'An error occurred while toggling the favorite.'}, 500


def update_favorite_group(favorite_id, group_id):
    """
    Move a favorite to a different group.
    
    Args:
        favorite_id: The favorite ID to update
        group_id: The group ID (can be None for ungrouped)
    
    Returns:
        tuple: (response_dict, status_code)
    """
    if not current_user.is_authenticated:
        return {'error': 'authentication required'}, 401
    
    try:
        fav = Favorite.query.filter_by(id=favorite_id, user_id=current_user.id).first()
        if not fav:
            return {'error': 'favorite not found'}, 404
        
        # Validate group_id if not None
        if group_id is not None:
            group = FavoriteGroup.query.filter_by(id=group_id, user_id=current_user.id).first()
            if not group:
                return {'error': 'group not found'}, 404
        
        fav.group_id = group_id
        db.session.commit()
        
        return {'status': 'ok', 'groupId': group_id}, 200
    
    except IntegrityError:
        db.session.rollback()
        return {'error': 'Unable to update favorite group. The specified group is not accessible.'}, 409
    except Exception as e:
        logger.error(f'Error updating favorite group for favorite_id {favorite_id}: {str(e)}', exc_info=True)
        db.session.rollback()
        return {'error': 'An error occurred while updating the favorite group.'}, 500


def get_favorite_groups():
    """
    Get all favorite groups for the current user with counts.
    
    Returns:
        list: List of group dicts with 'id', 'name', 'count'
    """
    if not current_user.is_authenticated:
        return []
    
    try:
        groups = FavoriteGroup.query.filter_by(user_id=current_user.id).order_by(FavoriteGroup.name).all()
        
        # Get counts for all groups in a single query
        counts = db.session.query(
            Favorite.group_id,
            func.count(Favorite.id).label('count')
        ).filter(
            Favorite.user_id == current_user.id
        ).group_by(Favorite.group_id).all()
        
        count_dict = {group_id: count for group_id, count in counts}
        
        result = [
            {
                'id': g.id,
                'name': g.name,
                'count': count_dict.get(g.id, 0)
            }
            for g in groups
        ]
        
        # Add ungrouped count at the beginning
        ungrouped_count = count_dict.get(None, 0)
        result.insert(0, {'id': None, 'name': 'Ungrouped', 'count': ungrouped_count})
        
        return result
    except Exception as e:
        logger.error(f'Error getting favorite groups: {str(e)}', exc_info=True)
        raise


def create_favorite_group(name):
    """
    Create a new favorite group for the current user.
    
    Args:
        name: The group name
    
    Returns:
        tuple: (response_dict, status_code)
    """
    if not current_user.is_authenticated:
        return {'error': 'authentication required'}, 401
    
    if not isinstance(name, str) or not name.strip():
        return {'error': 'name cannot be empty'}, 400
    
    try:
        name = name.strip()
        if len(name) > 128:
            return {'error': 'name must be 128 characters or fewer'}, 400

        # Check if group already exists
        existing = FavoriteGroup.query.filter_by(user_id=current_user.id, name=name).first()
        if existing:
            return {'error': 'A group with this name already exists.'}, 409
        
        group = FavoriteGroup(user_id=current_user.id, name=name)
        db.session.add(group)
        db.session.commit()
        
        return {'status': 'ok', 'id': group.id, 'name': group.name}, 201
    except IntegrityError:
        db.session.rollback()
        return {'error': 'A group with this name already exists.'}, 409
    except Exception as e:
        logger.error(f'Error creating favorite group: {str(e)}', exc_info=True)
        db.session.rollback()
        return {'error': 'An error occurred while creating the favorite group.'}, 500


def delete_favorite_group(group_id):
    """
    Delete a favorite group (orphans its favorites).
    
    Args:
        group_id: The group ID to delete
    
    Returns:
        tuple: (response_dict, status_code)
    """
    if not current_user.is_authenticated:
        return {'error': 'authentication required'}, 401
    
    try:
        group = FavoriteGroup.query.filter_by(id=group_id, user_id=current_user.id).first()
        if not group:
            return {'error': 'group not found'}, 404
        
        # Orphan favorites (set group_id to None instead of deleting)
        Favorite.query.filter_by(group_id=group_id, user_id=current_user.id).update({'group_id': None})
        db.session.delete(group)
        db.session.commit()

        return {'status': 'ok'}, 200
    except IntegrityError:
        db.session.rollback()
        return {'error': 'Unable to delete favorite group. It may still have associated favorites.'}, 409
    except Exception as e:
        logger.error(f'Error deleting favorite group {group_id}: {str(e)}', exc_info=True)
        db.session.rollback()
        return {'error': 'Error deleting favorite group.'}, 500
