# coding: utf-8
#
# Copyright 2020 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One-off jobs for suggestions."""

from __future__ import absolute_import  # pylint: disable=import-only-modules
from __future__ import unicode_literals  # pylint: disable=import-only-modules

import ast

from constants import constants
from core import jobs
from core.domain import html_validation_service
from core.domain import suggestion_services
from core.platform import models
import feconf

(feedback_models, suggestion_models, user_models,) = (
    models.Registry.import_models(
        [models.NAMES.feedback, models.NAMES.suggestion, models.NAMES.user]))
transaction_services = models.Registry.import_transaction_services()


class QuestionSuggestionMigrationJobManager(jobs.BaseMapReduceOneOffJobManager):
    """A reusable one-time job that can be used to migrate state schema
    versions of question suggestions.

    This job will create domain objects out of the models. The object conversion
    process of a suggestion automatically performs schema updating. This
    job persists that conversion work, keeping question suggestions up-to-date
    and improving the load time of question suggestions.
    """

    @classmethod
    def entity_classes_to_map_over(cls):
        return [suggestion_models.GeneralSuggestionModel]

    @staticmethod
    def map(item):
        if item.deleted or item.suggestion_type != (
                feconf.SUGGESTION_TYPE_ADD_QUESTION):
            return

        try:
            # Suggestion class itself updates the question state dict of the
            # suggestion while initializing the object.
            suggestion = suggestion_services.get_suggestion_from_model(item)
        except Exception as e:
            yield ('MIGRATION_FAILURE', (item.id, e))
            return

        try:
            suggestion.validate()
        except Exception as e:
            yield ('POST_MIGRATION_VALIDATION_FALIURE', (item.id, e))
            return

        item.change_cmd = suggestion.change.to_dict()
        item.put_for_bot()

        yield ('SUCCESS', item.id)

    @staticmethod
    def reduce(key, value):
        if key == 'SUCCESS':
            value = len(value)
        yield (key, value)


class SuggestionMathRteAuditOneOffJob(jobs.BaseMapReduceOneOffJobManager):
    """Job that checks for existence of math components in the suggestions."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [suggestion_models.GeneralSuggestionModel]

    @staticmethod
    def map(item):
        suggestion = suggestion_services.get_suggestion_from_model(item)
        html_string_list = suggestion.get_all_html_content_strings()
        html_string = ''.join(html_string_list)
        if (
                html_validation_service.check_for_math_component_in_html(
                    html_string)):
            yield ('Suggestion with Math', item.id)

    @staticmethod
    def reduce(key, values):
        yield (
            '%d suggestions have Math components in them, with IDs: %s' % (
                len(values), values))


class SuggestionSvgFilenameValidationOneOffJob(
        jobs.BaseMapReduceOneOffJobManager):
    """Job that checks the html content of a suggestion and validates the
    svg_filename fields in each math rich-text components."""

    _ERROR_KEY = 'invalid-math-content-attribute-in-math-tag'
    _INVALID_SVG_FILENAME_KEY = (
        'invalid-svg-filename-attribute-in-math-expression')

    @classmethod
    def entity_classes_to_map_over(cls):
        return [suggestion_models.GeneralSuggestionModel]

    @staticmethod
    def map(item):
        if item.target_type != feconf.ENTITY_TYPE_EXPLORATION:
            return
        if item.suggestion_type != (
                feconf.SUGGESTION_TYPE_EDIT_STATE_CONTENT):
            return
        suggestion = suggestion_services.get_suggestion_from_model(item)
        html_string_list = suggestion.get_all_html_content_strings()
        html_string = ''.join(html_string_list)
        invalid_math_tags = (
            html_validation_service.
            validate_math_tags_in_html_with_attribute_math_content(html_string))
        if len(invalid_math_tags) > 0:
            yield (
                SuggestionSvgFilenameValidationOneOffJob._ERROR_KEY,
                item.id)
            return
        math_tags_with_invalid_svg_filename = (
            html_validation_service.validate_svg_filenames_in_math_rich_text(
                feconf.ENTITY_TYPE_EXPLORATION, item.target_id, html_string))
        if len(math_tags_with_invalid_svg_filename) > 0:
            yield (
                SuggestionSvgFilenameValidationOneOffJob.
                _INVALID_SVG_FILENAME_KEY, (
                    item.id, math_tags_with_invalid_svg_filename))

    @staticmethod
    def reduce(key, values):
        if key == (
                SuggestionSvgFilenameValidationOneOffJob.
                _INVALID_SVG_FILENAME_KEY):
            final_values = [ast.literal_eval(value) for value in values]
            number_of_math_tags_with_invalid_svg_filename = 0
            for suggestion_id, math_tags_with_invalid_svg_filename in (
                    final_values):
                number_of_math_tags_with_invalid_svg_filename += len(
                    math_tags_with_invalid_svg_filename)
                yield (
                    'math tags with no SVGs in suggestion with ID %s' % (
                        suggestion_id), math_tags_with_invalid_svg_filename)
            final_value_dict = {
                'number_of_suggestions_with_no_svgs': len(final_values),
                'number_of_math_tags_with_invalid_svg_filename': (
                    number_of_math_tags_with_invalid_svg_filename),
            }
            yield ('Overall result', final_value_dict)
        else:
            yield (key, values)


class PopulateContributionStatsOneOffJob(
        jobs.BaseMapReduceOneOffJobManager):
    """A reusable one-time job that may be used to initialize, or regenerate,
    the reviewer and suggestion counts in the CommunityContributionStatsModel.
    """

    _VALIDATION_ERROR_KEY = 'community_contribution_stats_validation_error'
    ITEM_TYPE_REVIEWER = 'reviewer'
    ITEM_TYPE_SUGGESTION = 'suggestion'
    ITEM_CATEGORY_QUESTION = 'question'
    ITEM_CATEGORY_TRANSLATION = 'translation'
    KEY_DELIMITER = '.'

    @classmethod
    def entity_classes_to_map_over(cls):
        return [
            suggestion_models.GeneralSuggestionModel,
            user_models.UserContributionRightsModel
        ]

    @staticmethod
    def map(item):
        if item.deleted:
            return

        if isinstance(item, suggestion_models.GeneralSuggestionModel):
            # Exit early if the suggestion type is not a part of the
            # Contributor Dashboard or if the suggestion is not currently in
            # review.
            if item.suggestion_type == (
                    feconf.SUGGESTION_TYPE_EDIT_STATE_CONTENT) or (
                        item.status != suggestion_models.STATUS_IN_REVIEW):
                return
            suggestion = suggestion_services.get_suggestion_from_model(item)
            yield ('%s%s%s%s%s' % (
                PopulateContributionStatsOneOffJob.ITEM_TYPE_SUGGESTION,
                PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                suggestion.suggestion_type,
                PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                suggestion.language_code), 1)

        else:
            if item.can_review_translation_for_language_codes:
                for language_code in (
                        item.can_review_translation_for_language_codes):
                    yield ('%s%s%s%s%s' % (
                        PopulateContributionStatsOneOffJob.ITEM_TYPE_REVIEWER,
                        PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                        (
                            PopulateContributionStatsOneOffJob
                            .ITEM_CATEGORY_TRANSLATION
                        ),
                        PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                        language_code), 1)
            if item.can_review_questions:
                yield ('%s%s%s%s%s' % (
                    PopulateContributionStatsOneOffJob.ITEM_TYPE_REVIEWER,
                    PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                    PopulateContributionStatsOneOffJob.ITEM_CATEGORY_QUESTION,
                    PopulateContributionStatsOneOffJob.KEY_DELIMITER,
                    constants.DEFAULT_LANGUAGE_CODE), 1)

    @staticmethod
    def reduce(key, values):

        @transaction_services.run_in_transaction_wrapper
        def _update_community_contribution_stats_transactional(
                key, count_value):
            """Updates the CommunityContributionStatsModel according to the
            given key and count. The key contains the information for which
            count to update with the given count value. The model GET and PUT
            must be done in a transaction to avoid loss of updates that come in
            rapid succession.

            Args:
                key: str. A key containing the information regarding which count
                    to update.
                count_value: int. The count value for the given key.

            Returns:
                tuple(str, str). A 2-tuple that consists of the key and
                count_value to yield outside of the transaction.
            """
            item_type, item_category, language_code = key.split(
                PopulateContributionStatsOneOffJob.KEY_DELIMITER)

            stats_model = (
                suggestion_models.CommunityContributionStatsModel.get())

            # Update the reviewer counts.
            if item_type == (
                    PopulateContributionStatsOneOffJob.ITEM_TYPE_REVIEWER):
                if item_category == (
                        PopulateContributionStatsOneOffJob
                        .ITEM_CATEGORY_QUESTION):
                    stats_model.question_reviewer_count = count_value
                elif item_category == (
                        PopulateContributionStatsOneOffJob
                        .ITEM_CATEGORY_TRANSLATION):
                    (
                        stats_model
                        .translation_reviewer_counts_by_lang_code[language_code]
                    ) = count_value
            # Update the suggestion counts.
            else:
                if item_category == (
                        feconf.SUGGESTION_TYPE_ADD_QUESTION):
                    stats_model.question_suggestion_count = count_value
                elif item_category == (
                        feconf.SUGGESTION_TYPE_TRANSLATE_CONTENT):
                    (
                        stats_model
                        .translation_suggestion_counts_by_lang_code[
                            language_code]
                    ) = count_value

            stats_model.put()
            return key, count_value

        key_from_transaction, count_value_from_transaction = (
            _update_community_contribution_stats_transactional(
                key, len(values)))

        # Only yield the values from the transactions.
        yield (key_from_transaction, count_value_from_transaction)


class PopulateFinalReviewerIdOneOffJob(jobs.BaseMapReduceOneOffJobManager):
    """One-off job to populate final_reviewer_id property in the
    suggestion models which do not have but are expected to have one.
    """

    @classmethod
    def entity_classes_to_map_over(cls):
        return [suggestion_models.GeneralSuggestionModel]

    @staticmethod
    def map(item):
        if item.deleted:
            yield ('DELETED_MODELS', item.id)
            return

        if not (item.status in [
                suggestion_models.STATUS_ACCEPTED,
                suggestion_models.STATUS_REJECTED] and (
                    item.final_reviewer_id is None)):
            yield ('UNCHANGED_MODELS', item.id)
            return

        message_status = feedback_models.STATUS_CHOICES_FIXED
        if item.status == suggestion_models.STATUS_REJECTED:
            message_status = feedback_models.STATUS_CHOICES_IGNORED

        message_model_class = feedback_models.GeneralFeedbackMessageModel
        message_models = message_model_class.query(
            message_model_class.thread_id == item.id,
            message_model_class.updated_status == message_status).fetch()

        if not message_models:
            yield ('FAILED_NONE_MESSAGE_MODEL', (item.id, item.status))
            return

        if len(message_models) != 1:
            yield ('FAILED_MULTIPLE_MESSAGE_MODEL', (item.id, item.status))
            return

        item.final_reviewer_id = message_models[0].author_id

        item.put_for_bot()
        yield ('CHANGED_MODELS', item.id)

    @staticmethod
    def reduce(key, values):
        if key in ['CHANGED_MODELS', 'UNCHANGED_MODELS', 'DELETED_MODELS']:
            yield (key, len(values))
        else:
            yield (key, values)


class ContentSuggestionFormatUpdateOneOffJob(
        jobs.BaseMapReduceOneOffJobManager):
    """Job that migrates the format of content suggestions."""

    @classmethod
    def entity_classes_to_map_over(cls):
        return [suggestion_models.GeneralSuggestionModel]

    @staticmethod
    def map(item):
        if item.suggestion_type != feconf.SUGGESTION_TYPE_EDIT_STATE_CONTENT:
            return

        changes_made = False
        if 'old_value' in item.change_cmd:
            del item.change_cmd['old_value']
            changes_made = True
            yield ('CHANGED - Removed old_value', item.id)
        if 'content_id' in item.change_cmd['new_value']:
            del item.change_cmd['new_value']['content_id']
            changes_made = True
            yield ('CHANGED - Removed content_id', item.id)

        # Validate the eventual suggestion format.
        try:
            assert set(item.change_cmd.keys()) == set([
                'state_name', 'cmd', 'new_value', 'property_name']), (
                    'Bad change_cmd keys')
            assert item.change_cmd['cmd'] == 'edit_state_property', (
                'Wrong cmd in change_cmd')
            assert item.change_cmd['property_name'] == 'content', (
                'Bad property name')
            assert item.change_cmd['new_value'].keys() == ['html'], (
                'Bad new_value keys')
        except AssertionError as e:
            yield ('Failed assertion', '%s %s' % (item.id, e))

        if changes_made:
            item.put_for_bot()
            yield ('SUCCESS - Updated suggestion', item.id)

    @staticmethod
    def reduce(key, values):
        yield (key, values)
