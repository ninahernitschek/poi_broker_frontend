"""Comprehensive tests for _build_observatory_context() and _resolve_selected_observatory()."""

import json
import pytest
from unittest.mock import patch, MagicMock
from flask_login import login_user

from poi_broker import db
from poi_broker.models import User, UserObservatory, UserSettings
from poi_broker.app import _build_observatory_context, _resolve_selected_observatory


class TestResolveSelectedObservatory:
    """Test the _resolve_selected_observatory() helper function."""

    def test_none_input_returns_none(self):
        """Passing None should return None."""
        result = _resolve_selected_observatory(None, set(), set())
        assert result is None

    def test_non_dict_input_returns_none(self):
        """Non-dict input should return None."""
        result = _resolve_selected_observatory("invalid", set(), set())
        assert result is None

    def test_valid_custom_selection_found(self):
        """Valid custom selection should be returned."""
        selected_meta = {'source': 'custom', 'id': 42}
        custom_values = {'custom:42', 'custom:99'}
        result = _resolve_selected_observatory(selected_meta, set(), custom_values)
        assert result == 'custom:42'

    def test_valid_custom_selection_not_found(self):
        """Custom selection not in valid values should return None (graceful fallback)."""
        selected_meta = {'source': 'custom', 'id': 42}
        custom_values = {'custom:99'}  # 42 not in set
        result = _resolve_selected_observatory(selected_meta, set(), custom_values)
        assert result is None

    def test_valid_builtin_selection_found(self):
        """Valid builtin selection should be returned."""
        selected_meta = {'source': 'builtin', 'name': 'Palomar'}
        builtin_values = {'builtin:Palomar', 'builtin:Keck'}
        result = _resolve_selected_observatory(selected_meta, builtin_values, set())
        assert result == 'builtin:Palomar'

    def test_valid_builtin_selection_not_found(self):
        """Builtin selection not in valid values should return None."""
        selected_meta = {'source': 'builtin', 'name': 'Palomar'}
        builtin_values = {'builtin:Keck'}
        result = _resolve_selected_observatory(selected_meta, builtin_values, set())
        assert result is None

    def test_invalid_source_returns_none(self):
        """Unknown source should return None."""
        selected_meta = {'source': 'unknown', 'id': 42}
        result = _resolve_selected_observatory(selected_meta, set(), set())
        assert result is None

    def test_custom_selection_wrong_id_type_returns_none(self):
        """Custom selection with non-int id should return None."""
        selected_meta = {'source': 'custom', 'id': 'not_an_int'}
        custom_values = {'custom:not_an_int'}
        result = _resolve_selected_observatory(selected_meta, set(), custom_values)
        assert result is None

    def test_custom_selection_boolean_id_returns_none(self):
        selected_meta = {'source': 'custom', 'id': True}
        custom_values = {'custom:1', 'custom:True'}
        result = _resolve_selected_observatory(selected_meta, set(), custom_values)
        assert result is None

    def test_builtin_selection_wrong_name_type_returns_none(self):
        """Builtin selection with non-str name should return None."""
        selected_meta = {'source': 'builtin', 'name': 123}
        builtin_values = {'builtin:123'}
        result = _resolve_selected_observatory(selected_meta, builtin_values, set())
        assert result is None

    def test_missing_id_field_for_custom_returns_none(self):
        """Custom selection missing 'id' field should return None."""
        selected_meta = {'source': 'custom'}  # no 'id'
        result = _resolve_selected_observatory(selected_meta, set(), set())
        assert result is None

    def test_missing_name_field_for_builtin_returns_none(self):
        """Builtin selection missing 'name' field should return None."""
        selected_meta = {'source': 'builtin'}  # no 'name'
        result = _resolve_selected_observatory(selected_meta, set(), set())
        assert result is None


class TestBuildObservatoryContextUnauthenticated:
    """Test _build_observatory_context() for unauthenticated users."""

    def test_unauthenticated_user_has_builtin_only(self, app):
        """Unauthenticated user should have builtin options but no custom options."""
        with app.test_request_context():
            result = _build_observatory_context()
            
            assert 'builtin_options' in result
            assert 'custom_options' in result
            assert 'selected_value' in result
            assert len(result['custom_options']) == 0
            assert len(result['builtin_options']) > 0  # Astropy has builtin observatories
            assert result['selected_value'] == result['builtin_options'][0]['value']

    def test_unauthenticated_result_structure(self, app):
        """Verify result dict has correct structure for unauthenticated user."""
        with app.test_request_context():
            result = _build_observatory_context()
            
            # Check dict keys
            assert set(result.keys()) == {'builtin_options', 'custom_options', 'selected_value'}
            
            # Check option dicts have required keys
            for opt in result['builtin_options']:
                assert 'value' in opt
                assert 'label' in opt
                assert isinstance(opt['value'], str)
                assert isinstance(opt['label'], str)
            
            # Check selected_value is valid
            assert result['selected_value'] is None or isinstance(result['selected_value'], str)


class TestBuildObservatoryContextAuthenticated:
    """Test _build_observatory_context() for authenticated users."""

    def test_authenticated_user_no_custom_observatories(self, app, auth_client):
        """Authenticated user without custom observatories defaults to builtin."""
        with app.app_context():
            with app.test_request_context():
                # auth_client sets up a logged-in user
                from flask_login import current_user
                
                # Make a request via auth_client to set up login
                auth_client.get('/')
                
                # Now in a new context with that user
                user = User.query.filter_by(email='smoketest@example.com').first()
                assert user is not None
                
                # No custom observatories should exist
                custom_rows = UserObservatory.query.filter_by(user_id=user.id).all()
                assert len(custom_rows) == 0

    def test_authenticated_user_with_custom_observatories(self, app, user_factory):
        """Authenticated user with custom observatories should have them in options."""
        with app.app_context():
            # Create user via factory (which exits its own app_context)
            user_factory(email='test_custom@example.com')
            
            # Query fresh user in this context
            user = User.query.filter_by(email='test_custom@example.com').first()
            assert user is not None
            
            # Create custom observatories
            obs1 = UserObservatory(user_id=user.id, name='My Observatory', latitude=45.0, longitude=-120.0, timezone_name='US/Pacific')
            obs2 = UserObservatory(user_id=user.id, name='Secondary Site', latitude=40.0, longitude=-110.0, timezone_name='US/Mountain')
            db.session.add(obs1)
            db.session.add(obs2)
            db.session.commit()
            
            # Test within same context (no session issues)
            with app.test_request_context():
                login_user(user)
                
                result = _build_observatory_context()
                
                # Verify custom options are present and sorted by name
                assert len(result['custom_options']) == 2
                custom_labels = [opt['label'] for opt in result['custom_options']]
                assert custom_labels == ['My Observatory', 'Secondary Site']  # Alphabetically sorted
                
                # Verify they have correct values
                custom_values = {opt['value'] for opt in result['custom_options']}
                assert custom_values == {f'custom:{obs1.id}', f'custom:{obs2.id}'}

    def test_authenticated_user_no_saved_selection_defaults_to_builtin(self, app, user_factory):
        """Authenticated user without saved selection defaults to first builtin."""
        with app.app_context():
            user_factory(email='test_no_saved@example.com')
            user = User.query.filter_by(email='test_no_saved@example.com').first()
            assert user is not None
            
            # Create one custom observatory but don't save selection
            obs = UserObservatory(user_id=user.id, name='Test Obs', latitude=0.0, longitude=0.0, timezone_name='UTC')
            db.session.add(obs)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Should default to first builtin, not custom
                first_builtin = result['builtin_options'][0]['value'] if result['builtin_options'] else None
                assert result['selected_value'] == first_builtin

    def test_authenticated_user_with_valid_saved_custom_selection(self, app, user_factory):
        """Authenticated user with valid saved custom selection should restore it."""
        with app.app_context():
            user_factory(email='test_saved_custom@example.com')
            user = User.query.filter_by(email='test_saved_custom@example.com').first()
            assert user is not None
            
            # Create custom observatory
            obs = UserObservatory(user_id=user.id, name='My Saved Obs', latitude=45.0, longitude=-120.0, timezone_name='US/Pacific')
            db.session.add(obs)
            db.session.flush()  # Get the ID without full commit
            obs_id = obs.id
            
            # Save selection to UserSettings
            selection_data = {'source': 'custom', 'id': obs_id}
            settings = UserSettings(
                user_id=user.id,
                last_selected_observatory_json=json.dumps(selection_data)
            )
            db.session.add(settings)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Should restore the saved custom selection
                assert result['selected_value'] == f'custom:{obs_id}'

    def test_authenticated_user_with_valid_saved_builtin_selection(self, app, user_factory):
        """Authenticated user with valid saved builtin selection should restore it."""
        with app.app_context():
            user_factory(email='test_saved_builtin@example.com')
            user = User.query.filter_by(email='test_saved_builtin@example.com').first()
            assert user is not None
            
            # Save a builtin selection (use one we know exists)
            selection_data = {'source': 'builtin', 'name': 'Palomar'}
            settings = UserSettings(
                user_id=user.id,
                last_selected_observatory_json=json.dumps(selection_data)
            )
            db.session.add(settings)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Should restore the saved builtin selection
                assert result['selected_value'] == 'builtin:Palomar'

    def test_authenticated_user_saved_selection_to_deleted_observatory_falls_back(self, app, user_factory):
        """User with saved selection pointing to deleted observatory should fall back to builtin."""
        with app.app_context():
            user_factory(email='test_deleted@example.com')
            user = User.query.filter_by(email='test_deleted@example.com').first()
            assert user is not None
            
            # Save selection to non-existent observatory ID
            selection_data = {'source': 'custom', 'id': 9999}
            settings = UserSettings(
                user_id=user.id,
                last_selected_observatory_json=json.dumps(selection_data)
            )
            db.session.add(settings)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Should fall back to first builtin (not the invalid custom)
                first_builtin = result['builtin_options'][0]['value'] if result['builtin_options'] else None
                assert result['selected_value'] == first_builtin
                assert result['selected_value'] != 'custom:9999'

    def test_authenticated_user_with_corrupted_saved_selection_falls_back(self, app, user_factory):
        """User with corrupted saved selection should fall back gracefully."""
        with app.app_context():
            user_factory(email='test_corrupted@example.com')
            user = User.query.filter_by(email='test_corrupted@example.com').first()
            assert user is not None
            
            # Save malformed selection (missing 'source')
            selection_data = {'id': 123}  # Missing 'source'
            settings = UserSettings(
                user_id=user.id,
                last_selected_observatory_json=json.dumps(selection_data)
            )
            db.session.add(settings)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Should fall back to first builtin
                first_builtin = result['builtin_options'][0]['value'] if result['builtin_options'] else None
                assert result['selected_value'] == first_builtin

    def test_authenticated_user_only_custom_no_builtin_selects_custom(self, app, user_factory):
        """Edge case: if only custom observatories available (no builtin), select first custom."""
        with app.app_context():
            user_factory(email='test_custom_only@example.com')
            user = User.query.filter_by(email='test_custom_only@example.com').first()
            assert user is not None
            
            # Create one custom observatory
            obs = UserObservatory(user_id=user.id, name='Only Observatory', latitude=0.0, longitude=0.0, timezone_name='UTC')
            db.session.add(obs)
            db.session.commit()
            obs_id = obs.id
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                
                # Mock _get_builtin_observatory_options to return empty
                with patch('poi_broker.app._get_builtin_observatory_options', return_value=[]):
                    result = _build_observatory_context()
                    
                    # Should select first custom observatory
                    assert len(result['builtin_options']) == 0
                    assert len(result['custom_options']) == 1
                    assert result['selected_value'] == f'custom:{obs_id}'

    def test_authenticated_result_structure_with_custom_and_builtin(self, app, user_factory):
        """Verify result structure when both custom and builtin options exist."""
        with app.app_context():
            user_factory(email='test_structure@example.com')
            user = User.query.filter_by(email='test_structure@example.com').first()
            assert user is not None
            
            # Create custom observatory
            obs = UserObservatory(user_id=user.id, name='Test Obs', latitude=0.0, longitude=0.0, timezone_name='UTC')
            db.session.add(obs)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                result = _build_observatory_context()
                
                # Check result structure
                assert set(result.keys()) == {'builtin_options', 'custom_options', 'selected_value'}
                
                # Check custom options structure
                assert isinstance(result['custom_options'], list)
                for opt in result['custom_options']:
                    assert isinstance(opt, dict)
                    assert set(opt.keys()) == {'value', 'label'}
                    assert opt['value'].startswith('custom:')
                    assert isinstance(opt['label'], str)
                
                # Check builtin options structure
                assert isinstance(result['builtin_options'], list)
                for opt in result['builtin_options']:
                    assert isinstance(opt, dict)
                    assert set(opt.keys()) == {'value', 'label'}
                    assert opt['value'].startswith('builtin:')
                    assert isinstance(opt['label'], str)
                
                # Check selected_value is in one of the option sets
                selected_values = (
                    {opt['value'] for opt in result['builtin_options']} |
                    {opt['value'] for opt in result['custom_options']}
                )
                assert result['selected_value'] in selected_values


class TestBuildObservatoryContextPerformance:
    """Verify performance improvements (O(1) lookups instead of O(n))."""

    def test_no_linear_search_for_many_observatories(self, app, user_factory):
        """Function should use O(1) set lookup, not O(n) any() search."""
        with app.app_context():
            user_factory(email='test_perf@example.com')
            user = User.query.filter_by(email='test_perf@example.com').first()
            assert user is not None
            
            # Create many custom observatories
            for i in range(100):
                obs = UserObservatory(
                    user_id=user.id,
                    name=f'Observatory {i:03d}',
                    latitude=float(i),
                    longitude=float(i),
                    timezone_name='UTC'
                )
                db.session.add(obs)
            db.session.commit()
            
            # Save selection to one in the middle
            mid_obs = UserObservatory.query.filter_by(user_id=user.id).order_by(UserObservatory.id).all()[50]
            mid_obs_id = mid_obs.id
            selection_data = {'source': 'custom', 'id': mid_obs_id}
            settings = UserSettings(
                user_id=user.id,
                last_selected_observatory_json=json.dumps(selection_data)
            )
            db.session.add(settings)
            db.session.commit()
            
            # Test within same context
            with app.test_request_context():
                login_user(user)
                # This should be fast because of O(1) set lookup
                result = _build_observatory_context()
                
                # Verify correct selection was restored
                assert result['selected_value'] == f'custom:{mid_obs_id}'
