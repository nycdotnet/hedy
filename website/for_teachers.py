import json
from flask_babel import gettext
import hedy
import hedyweb
from .achievements import Achievements
from website.auth import requires_login, is_teacher, is_admin, current_user, validate_student_signup_data, \
    store_new_student_account
import utils
import uuid
from flask import g, request, jsonify, session
from flask_helpers import render_template
import os
import hedy_content
from .database import Database
from .website_module import WebsiteModule, route


class ForTeachersModule(WebsiteModule):
    def __init__(self, db: Database, achievements: Achievements):
        super().__init__('teachers', __name__, url_prefix='/for-teachers')
        self.db = db
        self.achievements = achievements

    @route('/', methods=['GET'])
    @requires_login
    def for_teachers_page(self, user):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('not_teacher'))

        welcome_teacher = session.get('welcome-teacher') or False
        session.pop('welcome-teacher', None)

        teacher_classes = self.db.get_teacher_classes(current_user()['username'], True)
        adventures = []
        for adventure in self.db.get_teacher_adventures(current_user()['username']):
            adventures.append(
                {'id': adventure.get('id'),
                 'name': adventure.get('name'),
                 'date': utils.localized_date_format(adventure.get('date')),
                 'level': adventure.get('level')
                 }
            )

        return render_template('for-teachers.html', current_page='my-profile', page_title=gettext('title_for-teacher'),
                               teacher_classes=teacher_classes,
                               teacher_adventures=adventures, welcome_teacher=welcome_teacher)

    @route('/manual', methods=['GET'])
    @requires_login
    def get_teacher_manual(self, user):
        page_translations = hedyweb.PageTranslations('for-teachers').get_page_translations(g.lang)
        return render_template('teacher-manual.html', current_page='my-profile', content=page_translations)

    @route('/class/<class_id>', methods=['GET'])
    @requires_login
    def get_class(self, user, class_id):
        if not is_teacher(user) and not is_admin(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_class_error'))
        Class = self.db.get_class(class_id)
        if not Class or (Class['teacher'] != user['username'] and not is_admin(user)):
            return utils.error_page(error=404, ui_message=gettext('no_such_class'))
        students = []

        for student_username in Class.get('students', []):
            student = self.db.user_by_username(student_username)
            programs = self.db.programs_for_user(student_username)
            # Fixme: The get_quiz_stats function requires a list of ids -> doesn't work on single string
            quiz_scores = self.db.get_quiz_stats([student_username])
            highest_quiz = max([x.get('level') for x in quiz_scores if x.get('finished')]) if quiz_scores else "-"
            students.append({
                'username': student_username,
                'last_login': student['last_login'],
                'programs': len(programs),
                'highest_level': highest_quiz
            })

        # Sort the students by their last login
        students = sorted(students, key=lambda d: d.get('last_login', 0), reverse=True)
        # After sorting: replace the number value by a string format date
        for student in students:
            student['last_login'] = utils.localized_date_format(student.get('last_login', 0))

        if utils.is_testing_request(request):
            return jsonify({'students': students, 'link': Class['link'], 'name': Class['name'], 'id': Class['id']})

        achievement = None
        if len(students) > 20:
            achievement = self.achievements.add_single_achievement(user['username'], "full_house")
        if achievement:
            achievement = json.dumps(achievement)

        invites = []
        for invite in self.db.get_class_invites(Class['id']):
            invites.append({'username': invite['username'],
                            'timestamp': utils.localized_date_format(invite['timestamp'], short_format=True),
                            'expire_timestamp': utils.localized_date_format(invite['ttl'], short_format=True)})

        return render_template('class-overview.html', current_page='my-profile',
                               page_title=gettext('title_class-overview'),
                               achievement=achievement, invites=invites,
                               class_info={'students': students,
                                           'link': os.getenv('BASE_URL') + '/hedy/l/' + Class['link'],
                                           'teacher': Class['teacher'], 'name': Class['name'], 'id': Class['id']})

    @route('/customize-class/<class_id>', methods=['GET'])
    @requires_login
    def get_class_info(self, user, class_id):
        if not is_teacher(user) and not is_admin(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_class_error'))
        Class = self.db.get_class(class_id)
        if not Class or (Class['teacher'] != user['username'] and not is_admin(user)):
            return utils.error_page(error=404, ui_message=gettext('no_such_class'))

        if hedy_content.Adventures(g.lang).has_adventures():
            adventures = hedy_content.Adventures(g.lang).get_adventure_keyname_name_levels()
        else:
            adventures = hedy_content.Adventures("en").get_adventure_keyname_name_levels()

        teacher_adventures = self.db.get_teacher_adventures(user['username'])
        customizations = self.db.get_class_customizations(class_id)

        return render_template('customize-class.html', page_title=gettext('title_customize-class'),
                               class_info={'name': Class['name'], 'id': Class['id'], 'teacher': Class['teacher']},
                               max_level=hedy.HEDY_MAX_LEVEL,
                               adventures=adventures,
                               teacher_adventures=teacher_adventures,
                               customizations=customizations,
                               current_page='my-profile')

    @route('/customize-class/<class_id>', methods=['DELETE'])
    @requires_login
    def delete_customizations(self, user, class_id):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_class_error'))
        Class = self.db.get_class(class_id)
        if not Class or Class['teacher'] != user['username']:
            return utils.error_page(error=404, ui_message=gettext('no_such_class'))

        self.db.delete_class_customizations(class_id)
        return {'success': gettext('customization_deleted')}, 200

    @route('/customize-class/<class_id>', methods=['POST'])
    @requires_login
    def update_customizations(self, user, class_id):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_class_error'))
        Class = self.db.get_class(class_id)
        if not Class or Class['teacher'] != user['username']:
            return utils.error_page(error=404, ui_message=gettext('no_such_class'))

        body = request.json
        # Validations
        if not isinstance(body, dict):
            return gettext('ajax_error'), 400
        if not isinstance(body.get('levels'), list):
            return "Levels must be a list", 400
        if not isinstance(body.get('adventures'), dict):
            return 'Adventures must be a dict', 400
        if not isinstance(body.get('teacher_adventures'), list):
            return 'Teacher adventures must be a list', 400
        if not isinstance(body.get('other_settings'), list):
            return 'Other settings must be a list', 400
        if not isinstance(body.get('opening_dates'), dict):
            return 'Opening dates must be a dict', 400
        if not isinstance(body.get('level_thresholds'), dict):
            return 'Level thresholds must be a dict', 400

        # Values are always strings from the front-end -> convert to numbers
        levels = [int(i) for i in body['levels']]

        opening_dates = body['opening_dates'].copy()
        for level, timestamp in body.get('opening_dates').items():
            if len(timestamp) < 1:
                opening_dates.pop(level)
            else:
                try:
                    opening_dates[level] = utils.datetotimeordate(timestamp)
                except:
                    return 'One or more of your opening dates is invalid', 400

        adventures = {}
        for name, adventure_levels in body['adventures'].items():
            adventures[name] = [int(i) for i in adventure_levels]

        level_thresholds = {}
        for name, value in body.get('level_thresholds').items():
            # We only manually check for the quiz threshold, if we add more -> generalize this code
            if name == 'quiz':
                try:
                    value = int(value)
                except:
                    return 'Quiz threshold value is invalid', 400
                if value < 0 or value > 100:
                    return 'Quiz threshold value is invalid', 400
            level_thresholds[name] = value

        customizations = {
            'id': class_id,
            'levels': levels,
            'opening_dates': opening_dates,
            'adventures': adventures,
            'teacher_adventures': body['teacher_adventures'],
            'other_settings': body['other_settings'],
            'level_thresholds': level_thresholds
        }

        self.db.update_class_customizations(customizations)

        achievement = self.achievements.add_single_achievement(user['username'], "my_class_my_rules")
        if achievement:
            return {'achievement': achievement, 'success': gettext('class_customize_success')}, 200
        return {'success': gettext('class_customize_success')}, 200

    @route('/create-accounts/<class_id>', methods=['GET'])
    @requires_login
    def create_accounts(self, user, class_id):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('not_teacher'))
        current_class = self.db.get_class(class_id)
        if not current_class or current_class.get('teacher') != user.get('username'):
            return utils.error_page(error=403, ui_message=gettext('no_such_class'))

        return render_template('create-accounts.html', current_class=current_class)

    @route('/create-accounts', methods=['POST'])
    @requires_login
    def store_accounts(self, user):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('not_teacher'))
        body = request.json

        # Validations
        if not isinstance(body, dict):
            return gettext('ajax_error'), 400
        if not isinstance(body.get('accounts'), list):
            return "accounts should be a list!", 400

        if len(body.get('accounts', [])) < 1:
            return gettext('no_accounts'), 400

        usernames = []

        # Validation for correct types and duplicates
        for account in body.get('accounts', []):
            validation = validate_student_signup_data(account)
            if validation:
                return validation, 400
            if account.get('username').strip().lower() in usernames:
                return {'error': gettext('unique_usernames'), 'value': account.get('username')}, 200
            usernames.append(account.get('username').strip().lower())

        # Validation for duplicates in the db
        classes = self.db.get_teacher_classes(user['username'], False)
        for account in body.get('accounts', []):
            if account.get('class') and account['class'] not in [i.get('name') for i in classes]:
                return "not your class", 404
            if self.db.user_by_username(account.get('username').strip().lower()):
                return {'error': gettext('usernames_exist'), 'value': account.get('username').strip().lower()}, 200

        # Now -> actually store the users in the db
        for account in body.get('accounts', []):
            # Set the current teacher language and keyword language as new account language
            account['language'] = g.lang
            account['keyword_language'] = g.keyword_lang
            store_new_student_account(self.db, account, user['username'])
            if account.get('class'):
                class_id = [i.get('id') for i in classes if i.get('name') == account.get('class')][0]
                self.db.add_student_to_class(class_id, account.get('username').strip().lower())
        return {'success': gettext('accounts_created')}, 200

    @route('/customize-adventure/view/<adventure_id>', methods=['GET'])
    @requires_login
    def view_adventure(self, user, adventure_id):
        if not is_teacher(user) and not is_admin(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_adventure_error'))
        adventure = self.db.get_adventure(adventure_id)
        if not adventure:
            return utils.error_page(error=404, ui_message=gettext('no_such_adventure'))
        if adventure['creator'] != user['username'] and not is_admin(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_adventure_error'))

        # Add level to the <pre> tag to let syntax highlighting know which highlighting we need!
        adventure['content'] = adventure['content'].replace("<pre>", "<pre class='no-copy-button' level='" + str(
            adventure['level']) + "'>")
        adventure['content'] = adventure['content'].format(**hedy_content.KEYWORDS.get(g.keyword_lang))

        return render_template('view-adventure.html', adventure=adventure,
                               page_title=gettext('title_view-adventure'), current_page='my-profile')

    @route('/customize-adventure/<adventure_id>', methods=['GET'])
    @requires_login
    def get_adventure_info(self, user, adventure_id):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_adventure_error'))
        adventure = self.db.get_adventure(adventure_id)
        if not adventure or adventure['creator'] != user['username']:
            return utils.error_page(error=404, ui_message=gettext('no_such_adventure'))

        # Now it gets a bit complex, we want to get the teacher classes as well as the customizations
        # This is a quite expensive retrieval, but we should be fine as this page is not called often
        # We only need the name, id and if it already has the adventure set as data to the front-end
        Classes = self.db.get_teacher_classes(user['username'])
        class_data = []
        for Class in Classes:
            temp = {'name': Class.get('name'), 'id': Class.get('id'), 'checked': False}
            customizations = self.db.get_class_customizations(Class.get('id'))
            if customizations and adventure_id in customizations.get('teacher_adventures', []):
                temp['checked'] = True
            class_data.append(temp)

        return render_template('customize-adventure.html', page_title=gettext('title_customize-adventure'),
                               adventure=adventure, class_data=class_data,
                               max_level=hedy.HEDY_MAX_LEVEL, current_page='my-profile')

    @route('/customize-adventure', methods=['POST'])
    @requires_login
    def update_adventure(self, user):
        body = request.json
        # Validations
        if not isinstance(body, dict):
            return gettext('ajax_error'), 400
        if not isinstance(body.get('id'), str):
            return gettext('adventure_id_invalid'), 400
        if not isinstance(body.get('name'), str):
            return gettext('adventure_name_invalid'), 400
        if not isinstance(body.get('level'), str):
            return gettext('level_invalid'), 400
        if not isinstance(body.get('content'), str):
            return gettext('content_invalid'), 400
        if len(body.get('content')) < 20:
            return gettext('adventure_length'), 400
        if not isinstance(body.get('public'), bool):
            return gettext('public_invalid'), 400
        if not isinstance(body.get('classes'), list):
            return gettext('classes_invalid'), 400

        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_adventure_error'))
        current_adventure = self.db.get_adventure(body['id'])
        if not current_adventure or current_adventure['creator'] != user['username']:
            return utils.error_page(error=404, ui_message=gettext('no_such_adventure'))

        adventures = self.db.get_teacher_adventures(user['username'])
        for adventure in adventures:
            if adventure['name'] == body['name'] and adventure['id'] != body['id']:
                return gettext('adventure_duplicate'), 400

        # We want to make sure the adventure is valid and only contains correct placeholders
        # Try to parse with our current language, if it fails -> return an error to the user
        try:
            body['content'].format(**hedy_content.KEYWORDS.get(g.keyword_lang))
        except:
            return gettext('something_went_wrong_keyword_parsing'), 400

        adventure = {
            'date': utils.timems(),
            'creator': user['username'],
            'name': body['name'],
            'level': body['level'],
            'content': body['content'],
            'public': body['public']
        }

        self.db.update_adventure(body['id'], adventure)

        # Once the adventure is correctly stored we have to update all class customizations
        # This is once again an expensive operation, we have to retrieve all teacher customizations
        # Then check if something is changed with the current situation, if so -> update in database
        Classes = self.db.get_teacher_classes(user['username'])
        for Class in Classes:
            # If so, the adventure should be in the class
            if Class.get('id') in body.get('classes', []):
                self.db.add_adventure_to_class_customizations(Class.get('id'), body.get('id'))
            else:
                self.db.remove_adventure_from_class_customizations(Class.get('id'), body.get('id'))

        return {'success': gettext('adventure_updated')}, 200

    @route('/customize-adventure/<adventure_id>', methods=['DELETE'])
    @requires_login
    def delete_adventure(self, user, adventure_id):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('retrieve_adventure_error'))
        adventure = self.db.get_adventure(adventure_id)
        if not adventure or adventure['creator'] != user['username']:
            return utils.error_page(error=404, ui_message=gettext('no_such_adventure'))

        self.db.delete_adventure(adventure_id)
        return {}, 200

    @route('/preview-adventure', methods=['POST'])
    def parse_preview_adventure(self):
        body = request.json
        try:
            code = body.get('code').format(**hedy_content.KEYWORDS.get(g.keyword_lang))
        except:
            return gettext('something_went_wrong_keyword_parsing'), 400
        return {'code': code}, 200

    @route('/create_adventure', methods=['POST'])
    @requires_login
    def create_adventure(self, user):
        if not is_teacher(user):
            return utils.error_page(error=403, ui_message=gettext('create_adventure'))

        body = request.json
        # Validations
        if not isinstance(body, dict):
            return gettext('ajax_error'), 400
        if not isinstance(body.get('name'), str):
            return gettext('adventure_name_invalid'), 400
        if len(body.get('name')) < 1:
            return gettext('adventure_empty'), 400

        adventures = self.db.get_teacher_adventures(user['username'])
        for adventure in adventures:
            if adventure['name'] == body['name']:
                return gettext('adventure_duplicate'), 400

        adventure = {
            'id': uuid.uuid4().hex,
            'date': utils.timems(),
            'creator': user['username'],
            'name': body['name'],
            'level': 1,
            'content': ""
        }

        self.db.store_adventure(adventure)
        return {'id': adventure['id']}, 200
