import json
from urllib.parse import quote

import frappe
import requests
from frappe.integrations.utils import create_request_log

from zoom_integration.utils import ZOOM_API_BASE_PATH, get_authenticated_headers_for_zoom

DEFAULT_PAGE_SIZE = 300  # Zoom API allows max 300 per page


def create_zoom_session(resource: str, body: dict) -> dict:
	"""Create a meeting or webinar. `resource` is 'meetings' or 'webinars'."""
	url = f"{ZOOM_API_BASE_PATH}/users/me/{resource}"
	headers = get_authenticated_headers_for_zoom()
	response = requests.post(url, headers=headers, data=json.dumps(body))

	if response.status_code == 201:
		data = response.json()
		create_request_log(data, is_remote_request=1, service_name="Zoom", status="Completed")
		return data

	log_failed_response(response)
	frappe.throw(f"Failed to create {resource[:-1]} on Zoom ({response.status_code}): {response.text}")


def update_zoom_session(resource: str, session_id: str, body: dict) -> None:
	url = f"{ZOOM_API_BASE_PATH}/{resource}/{session_id}"
	headers = get_authenticated_headers_for_zoom()
	response = requests.patch(url, headers=headers, data=json.dumps(body))

	if response.status_code != 204:
		frappe.throw(f"Failed to update {resource[:-1]} on Zoom: {response.text}")


def delete_zoom_session(resource: str, session_id: str) -> bool:
	"""Returns True if deleted on Zoom, False if it was already gone (code 3001)."""
	url = f"{ZOOM_API_BASE_PATH}/{resource}/{session_id}"
	headers = get_authenticated_headers_for_zoom()
	response = requests.delete(url, headers=headers)

	if response.status_code == 204:
		return True
	if response.json().get("code") == 3001:
		return False
	frappe.throw(f"Failed to delete {resource[:-1]} on Zoom: {response.text}")


def add_zoom_registrant(resource: str, session_id: str, body: dict) -> dict:
	url = f"{ZOOM_API_BASE_PATH}/{resource}/{session_id}/registrants"
	headers = get_authenticated_headers_for_zoom()
	response = requests.post(url, headers=headers, data=json.dumps(body))

	if response.status_code not in (200, 201):
		log_failed_response(response)
		frappe.throw(f"Failed to add registrant on Zoom ({response.status_code}): {response.text}")

	data = response.json()
	create_request_log(data, is_remote_request=1, service_name="Zoom", status="Completed")
	return data


def _paginate(url: str, item_key: str) -> list[dict]:
	"""Follow Zoom's next_page_token pagination and collect `item_key` items."""
	headers = get_authenticated_headers_for_zoom()
	items: list[dict] = []
	next_page_token = None

	while True:
		# rebuild from `url` each pass so the token replaces rather than stacks
		page_url = f"{url}&next_page_token={next_page_token}" if next_page_token else url

		response = requests.get(page_url, headers=headers, timeout=30)
		if response.status_code != 200:
			frappe.throw(f"Failed to fetch {item_key} from Zoom: {response.text}")

		data = response.json()
		items.extend(data.get(item_key, []))
		next_page_token = data.get("next_page_token")
		if not next_page_token:
			break

	return items


def get_zoom_registrants(resource: str, session_id: str) -> list[dict]:
	url = f"{ZOOM_API_BASE_PATH}/{resource}/{session_id}/registrants?page_size={DEFAULT_PAGE_SIZE}"
	return _paginate(url, "registrants")


def get_zoom_participants(resource: str, session_uuid: str) -> list[dict]:
	# ponytail: single-encoding the UUID covers the common case; double-encode
	# here if you hit meeting UUIDs that start with "/" or contain "//".
	encoded_uuid = quote(session_uuid, safe="")
	url = f"{ZOOM_API_BASE_PATH}/past_{resource}/{encoded_uuid}/participants?page_size={DEFAULT_PAGE_SIZE}"
	return _paginate(url, "participants")


def log_failed_response(response) -> None:
	"""Log a failed Zoom call. Its body is not always JSON (a gateway error is HTML or
	empty), and `create_request_log` parses a string as JSON, so the body goes in as text."""
	create_request_log(
		{"status_code": response.status_code},
		is_remote_request=1,
		service_name="Zoom",
		status="Failed",
		error=response.text,
	)
