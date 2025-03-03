from flask import g, request, jsonify
from flask_babel import gettext
import hedy
from config import config
from .achievements import Achievements
from .database import Database
from website.auth import requires_login, current_user, is_admin, send_email, email_base_url, requires_admin
import utils
import uuid

from .website_module import WebsiteModule, route

class ProgramsModule(WebsiteModule):
    def __init__(self, db: Database, achievements: Achievements):
        super().__init__('programs', __name__, url_prefix='/programs')
        self.db = db
        self.achievements = achievements

    @route('/list', methods=['GET'])
    @requires_login
    def list_programs(self, user):
        return {'programs': self.db.programs_for_user(user['username']).records}

    @route('/delete/', methods=['POST'])
    @requires_login
    def delete_program(self, user):
        body = request.json
        if not isinstance(body.get('id'), str):
            return 'program id must be a string', 400

        result = self.db.program_by_id(body['id'])

        if not result or (result['username'] != user['username'] and not is_admin(user)):
            return "", 404
        self.db.delete_program_by_id(body['id'])
        self.db.increase_user_program_count(user['username'], -1)

        # This only happens in the situation were a user deletes their favourite program -> Delete from public profile
        public_profile = self.db.get_public_profile_settings(current_user()['username'])
        if public_profile and 'favourite_program' in public_profile and public_profile['favourite_program'] == body['id']:
            self.db.set_favourite_program(user['username'], None)

        achievement = self.achievements.add_single_achievement(user['username'], "do_you_have_copy")
        resp = {'message': gettext('delete_success')}
        if achievement:
            resp['achievement'] = achievement
        return jsonify(resp)

    @route('/duplicate-check', methods=['POST'])
    def check_duplicate_program(self):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('name'), str):
            return 'name must be a string', 400

        if not current_user()['username']:
            return gettext('save_prompt'), 403

        programs = self.db.programs_for_user(current_user()['username'])
        for program in programs:
            if program['name'] == body['name']:
                return jsonify({'duplicate': True, 'message': gettext('overwrite_warning')})
        return jsonify({'duplicate': False})

    @route('/', methods=['POST'])
    @requires_login
    def save_program(self, user):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('code'), str):
            return 'code must be a string', 400
        if not isinstance(body.get('name'), str):
            return 'name must be a string', 400
        if not isinstance(body.get('level'), int):
            return 'level must be an integer', 400
        if not isinstance(body.get('shared'), bool):
            return 'shared must be a boolean', 400
        if 'adventure_name' in body:
            if not isinstance(body.get('adventure_name'), str):
                return 'if present, adventure_name must be a string', 400

        error = False
        try:
            hedy.transpile(body.get('code'), body.get('level'), g.lang)
        except:
            error = True
            if not body.get('force_save', True):
                return jsonify({'parse_error': True, 'message': gettext('save_parse_warning')})

        # We check if a program with a name `xyz` exists in the database for the username.
        # It'd be ideal to search by username & program name, but since DynamoDB doesn't allow searching for two indexes at the same time, this would require to create a special index to that effect, which is cumbersome.
        # For now, we bring all existing programs for the user and then search within them for repeated names.
        programs = self.db.programs_for_user(user['username']).records
        program_id = uuid.uuid4().hex
        program_public = body.get('shared')
        overwrite = False
        for program in programs:
            if program['name'] == body['name']:
                overwrite = True
                program_id = program['id']
                # If a program was already shared, keep it that way
                if program.get('public', False):
                    program_public = True
                break

        stored_program = {
            'id': program_id,
            'session': utils.session_id(),
            'date': utils.timems(),
            'lang': g.lang,
            'version': utils.version(),
            'level': body['level'],
            'code': body['code'],
            'name': body['name'],
            'username': user['username'],
            'public': 1 if program_public else 0,
            'error': error
        }

        if 'adventure_name' in body:
            stored_program['adventure_name'] = body['adventure_name']

        self.db.store_program(stored_program)
        if not overwrite:
            self.db.increase_user_program_count(user['username'])
        self.db.increase_user_save_count(user['username'])
        self.achievements.increase_count("saved")

        if self.achievements.verify_save_achievements(user['username'],
                                                    'adventure_name' in body and len(body['adventure_name']) > 2):
            return jsonify(
                {'message': gettext('save_success_detail'), 'name': body['name'], 'id': program_id, "achievements": self.achievements.get_earned_achievements()})
        return jsonify({'message': gettext('save_success_detail'), 'name': body['name'], 'id': program_id})

    @route('/share', methods=['POST'])
    @requires_login
    def share_unshare_program(self, user):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('id'), str):
            return 'id must be a string', 400
        if not isinstance(body.get('public'), bool):
            return 'public must be a boolean', 400

        result = self.db.program_by_id(body['id'])
        if not result or result['username'] != user['username']:
            return 'No such program!', 404

        # This only happens in the situation were a user un-shares their favourite program -> Delete from public profile
        public_profile = self.db.get_public_profile_settings(current_user()['username'])
        if public_profile and 'favourite_program' in public_profile and public_profile['favourite_program'] == body[
            'id']:
            self.db.set_favourite_program(user['username'], None)

        self.db.set_program_public_by_id(body['id'], bool(body['public']))
        achievement = self.achievements.add_single_achievement(user['username'], "sharing_is_caring")

        resp = {'id': body['id']}
        if bool(body['public']):
            resp['message'] = gettext('share_success_detail')
        else:
            resp['message'] = gettext('unshare_success_detail')
        if achievement:
            resp['achievement'] = achievement
        return jsonify(resp)

    @route('/submit', methods=['POST'])
    @requires_login
    def submit_program(self, user):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('id'), str):
            return 'id must be a string', 400

        result = self.db.program_by_id(body['id'])
        if not result or result['username'] != user['username']:
            return 'No such program!', 404

        self.db.submit_program_by_id(body['id'])
        self.db.increase_user_submit_count(user['username'])
        self.achievements.increase_count("submitted")

        if self.achievements.verify_submit_achievements(user['username']):
            return jsonify({"achievements": self.achievements.get_earned_achievements()})
        return jsonify({})

    @route('/set_favourite', methods=['POST'])
    @requires_login
    def set_favourite_program(self, user):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('id'), str):
            return 'id must be a string', 400

        result = self.db.program_by_id(body['id'])
        if not result or result['username'] != user['username']:
            return 'No such program!', 404

        if self.db.set_favourite_program(user['username'], body['id']):
            return jsonify({'message': gettext('favourite_success')})
        else:
            return "You can't set a favourite program without a public profile", 400

    @route('/set_hedy_choice', methods=['POST'])
    @requires_admin
    def set_hedy_choice(self, user):
        body = request.json
        if not isinstance(body, dict):
            return 'body must be an object', 400
        if not isinstance(body.get('id'), str):
            return 'id must be a string', 400
        if not isinstance(body.get('favourite'), int):
            return 'favourite must be a integer', 400

        favourite = True if body.get('favourite') == 1 else False

        result = self.db.program_by_id(body['id'])
        if not result:
            return 'No such program!', 404

        self.db.set_program_as_hedy_choice(body['id'], favourite)
        if favourite:
            return jsonify({'message': 'Program successfully set as a "Hedy choice" program.'}), 200
        return jsonify({'message': 'Program successfully removed as a "Hedy choice" program.'}), 200

    @route('/report', methods=['POST'])
    @requires_login
    def report_program(self, user):
        body = request.json

        # Make sure the program actually exists and is public
        program = self.db.program_by_id(body.get('id'))
        if not program or program.get('public') != 1:
            return gettext('report_failure'), 400

        link = email_base_url() + '/hedy/' + body.get('id') + '/view'
        send_email(config['email']['sender'], "The following program is reported by " + user['username'], link,
                    '<a href="' + link + '">Program link</a>')

        return {'message': gettext('report_success')}, 200

