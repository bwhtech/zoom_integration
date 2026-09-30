from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from zoom_integration import api
from zoom_integration.tests.zoom_fixtures import (
	CREATE_MEETING_RESPONSE,
	TEST_ZOOM_HEADERS,
	mock_response,
)


class TestRequestLogging(IntegrationTestCase):
	def test_access_token_is_never_written_to_the_request_log(self):
		existing = set(frappe.get_all("Integration Request", pluck="name"))

		with (
			patch.object(api, "get_authenticated_headers_for_zoom", return_value=TEST_ZOOM_HEADERS),
			patch.object(api, "requests") as mock_requests,
		):
			mock_requests.post.return_value = mock_response(201, CREATE_MEETING_RESPONSE)
			api.create_zoom_session("meetings", {"topic": "Logging Test"})

		logged = frappe.get_all(
			"Integration Request",
			filters={"name": ["not in", list(existing)]},
			fields=["name", "request_headers"],
		)
		self.assertTrue(logged, "expected the call to be logged")
		for row in logged:
			self.assertNotIn("Authorization", row.request_headers or "")

	def test_a_failure_with_a_non_json_body_is_logged_and_reported(self):
		existing = set(frappe.get_all("Integration Request", pluck="name"))

		with (
			patch.object(api, "get_authenticated_headers_for_zoom", return_value=TEST_ZOOM_HEADERS),
			patch.object(api, "requests") as mock_requests,
		):
			mock_requests.post.return_value = mock_response(502, text="<html>Bad Gateway</html>")
			with self.assertRaises(frappe.ValidationError) as raised:
				api.create_zoom_session("meetings", {"topic": "Logging Test"})

		self.assertIn("502", str(raised.exception))
		logged = frappe.get_all(
			"Integration Request",
			filters={"name": ["not in", list(existing)], "status": "Failed"},
			fields=["data", "error"],
		)
		self.assertEqual(len(logged), 1)
		self.assertIn("502", logged[0].data)
		self.assertIn("Bad Gateway", logged[0].error)
