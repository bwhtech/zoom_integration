# Copyright (c) 2025, Build With Hussain and contributors
# For license information, please see license.txt

import json
import time

import frappe
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import cint, format_datetime, getdate
from frappe.utils.data import convert_utc_to_timezone, get_datetime, get_time

from zoom_integration.api import log_failed_response
from zoom_integration.utils import ZOOM_API_BASE_PATH, get_authenticated_headers_for_zoom

ATTENDANCE_SYNC_BATCH_SIZE = 300  # Zoom API allows max 300 per page


class ZoomWebinar(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agenda: DF.SmallText | None
		attendance_synced: DF.Check
		date: DF.Date
		description: DF.TextEditor | None
		duration: DF.Duration
		send_zoom_registration_email: DF.Check
		start_time: DF.Time
		template: DF.Link | None
		timezone: DF.Autocomplete | None
		title: DF.Data
		zoom_event_id: DF.Data | None
		zoom_link: DF.Data | None
		zoom_ticket_id: DF.Data | None
		zoom_webinar_id: DF.Data | None
	# end: auto-generated types

	def before_insert(self):
		self.create_webinar_on_zoom()

	def create_webinar_on_zoom(self):
		if self.zoom_webinar_id:
			return  # Webinar already created on Zoom

		url = f"{ZOOM_API_BASE_PATH}/users/me/webinars"
		headers = get_authenticated_headers_for_zoom()
		body = json.dumps(
			{
				"topic": self.title,
				"agenda": self.agenda or self.title,
				"type": 5,  # Scheduled webinar
				"duration": cint(self.duration / 60) if self.duration else 60,
				"start_time": format_datetime(f"{self.date} {self.start_time}", "yyyy-MM-ddTHH:mm:ss"),
				"timezone": self.timezone or "Asia/Calcutta",
				"settings": {
					"host_video": True,
					"panelists_video": True,
					"practice_session": True,
					"hd_video": True,
					"approval_type": 0,  # Automatically approve
					"registration_type": 1,  # Register once and join anytime
					"audio": "both",  # Both telephony and VoIP
					"auto_recording": "cloud",  # Record webinar in the cloud, TODO: make this configurable
					"meeting_authentication": False,  # Disable authentication for simplicity
				},
				"registrants_email_notification": self.send_zoom_registration_email,
				"template_id": self.template or None,
			}
		)

		response = requests.post(url, headers=headers, data=body)
		if response.status_code == 201:
			data = response.json()
			self.zoom_link = data.get("join_url")
			self.zoom_webinar_id = data.get("id")
			frappe.msgprint(_("Webinar created successfully on Zoom."))
			create_request_log(data, is_remote_request=1, service_name="Zoom", status="Completed")
		else:
			log_failed_response(response)
			frappe.throw(f"Failed to create webinar on Zoom ({response.status_code}): {response.text}")

	def on_update(self):
		if not self.zoom_webinar_id:
			return
		self.update_webinar_on_zoom_if_applicable()

	def update_webinar_on_zoom_if_applicable(self):
		if self.flags.in_import or self.flags.in_migration:
			return  # Skip updates during import or migration

		if self.is_new():
			return

		before_save = self.get_doc_before_save()

		if not before_save:
			return

		current_start = get_time(self.get("start_time"))
		previous_start = get_time(before_save.get("start_time"))
		current_date = getdate(self.get("date"))
		previous_date = getdate(before_save.get("date"))

		zoom_related_field_not_updated = (
			self.title == before_save.title
			and self.agenda == before_save.agenda
			and self.duration == before_save.duration
			and self.timezone == before_save.timezone
			and current_date == previous_date
			and current_start == previous_start
		)
		if zoom_related_field_not_updated:
			return

		headers = get_authenticated_headers_for_zoom()

		is_webinar_plus = bool(self.zoom_event_id and self.zoom_ticket_id)

		if is_webinar_plus:
			url = f"{ZOOM_API_BASE_PATH}/zoom_events/events/{self.zoom_event_id}"
			body = json.dumps(
				{
					"topic": self.title,
					"agenda": self.agenda or self.title,
					"duration": cint(self.duration / 60) if self.duration else 60,
					"start_time": format_datetime(f"{self.date} {self.start_time}", "yyyy-MM-ddTHH:mm:ss"),
					"timezone": self.timezone or "Asia/Calcutta",
				}
			)
		else:
			# Regular Webinar: Use Zoom Webinar API
			url = f"{ZOOM_API_BASE_PATH}/webinars/{self.zoom_webinar_id}"
			body = json.dumps(
				{
					"topic": self.title,
					"agenda": self.agenda or self.title,
					"duration": cint(self.duration / 60) if self.duration else 60,
					"start_time": format_datetime(f"{self.date} {self.start_time}", "yyyy-MM-ddTHH:mm:ss"),
					"timezone": self.timezone or "Asia/Calcutta",
				}
			)

		response = requests.patch(url, headers=headers, data=body)
		if response.status_code == 204:
			frappe.msgprint(_("Webinar updated successfully on Zoom."))
		else:
			frappe.throw("Failed to update webinar on Zoom: {0}".format(response.text))

	def on_trash(self):
		if self.zoom_webinar_id:
			self.delete_webinar_on_zoom()

		# frappe.db.delete(
		# 	"Zoom Session Registration",
		# 	{"reference_doctype": self.doctype, "reference_name": self.name},
		# )

	def delete_webinar_on_zoom(self):
		url = f"{ZOOM_API_BASE_PATH}/webinars/{self.zoom_webinar_id}"
		headers = get_authenticated_headers_for_zoom()

		response = requests.delete(url, headers=headers)
		if response.status_code == 204:
			self.zoom_link = None
			self.zoom_webinar_id = None
			self.save()
			frappe.msgprint(frappe._("Webinar deleted successfully from Zoom."))
		elif response.json().get("code") == 3001:
			frappe.msgprint(
				frappe._(
					"Webinar not found on Zoom (may have already been deleted). Clearing local reference."
				)
			)
		else:
			frappe.throw(frappe._(f"Failed to delete webinar: {response.text}"))

	def add_registrant(
		self, email: str, first_name: str, last_name: str | None = None, additional_params: dict | None = None
	):
		if not self.zoom_webinar_id:
			frappe.throw(frappe._("Webinar not created on Zoom yet."))

		if not additional_params:
			additional_params = {}

		headers = get_authenticated_headers_for_zoom()
		is_webinar_plus = self.zoom_event_id and self.zoom_ticket_id

		if is_webinar_plus:
			url = f"{ZOOM_API_BASE_PATH}/zoom_events/events/{self.zoom_event_id}/tickets"
			body = json.dumps(
				{
					"tickets": [
						{
							"email": email,
							"first_name": first_name,
							"last_name": last_name or "N/A",
							"ticket_type_id": self.zoom_ticket_id,
							**additional_params,
						}
					]
				}
			)
		else:
			url = f"{ZOOM_API_BASE_PATH}/webinars/{self.zoom_webinar_id}/registrants"
			body = json.dumps(
				{
					"email": email,
					"first_name": first_name,
					"last_name": last_name or "N/A",
					**additional_params,
				}
			)

		response = requests.post(url, headers=headers, data=body)

		if response.status_code not in (200, 201):
			log_failed_response(response)
			frappe.throw(frappe._(f"Failed to add registrant ({response.status_code}): {response.text}"))

		data = response.json()
		create_request_log(data, is_remote_request=1, service_name="Zoom", status="Completed")

		if is_webinar_plus:
			ticket = data.get("tickets", [{}])[0]
			return {
				"registrant_id": ticket.get("ticket_id"),
				"join_url": ticket.get("event_join_link"),
				"email": ticket.get("email"),
			}

		return data

	@frappe.whitelist()
	def sync_attendance(self):
		try:
			self.sync_registrations_from_zoom()

			details = get_webinar_attendance_details(self.name)

			if not details:
				frappe.msgprint(_("No attendance records found for this webinar."))
				return

			frappe.publish_progress(
				percent=5,
				title="Creating Attendance Records",
				description=f"Creating records for {len(details)} unique attendees...",
			)

			# Process in batches to avoid memory issues
			batch_size = ATTENDANCE_SYNC_BATCH_SIZE
			processed_count = 0

			for i in range(0, len(details), batch_size):
				batch = details[i : i + batch_size]

				for attendance in batch:
					user_email = attendance.get("user_email")
					if not user_email:
						continue

					if frappe.db.exists(
						"Zoom Session Attendance Record",
						{
							"reference_doctype": self.doctype,
							"reference_name": self.name,
							"user_email": user_email,
						},
					):
						continue

					registration = frappe.db.get_value(
						"Zoom Session Registration",
						{
							"reference_doctype": self.doctype,
							"reference_name": self.name,
							"email": user_email,
						},
						"name",
					)

					try:
						doc = frappe.get_doc(
							{
								"doctype": "Zoom Session Attendance Record",
								"registration": registration,
								"reference_doctype": self.doctype,
								"reference_name": self.name,
								"user_email": user_email,
								"full_name": attendance.get("name"),
								"total_duration": attendance.get("total_duration"),
								"registrant_id": attendance.get("registrant_id"),
								"docstatus": 1,
							}
						)
						doc.insert(ignore_permissions=True)
						processed_count += 1
					except Exception as e:
						frappe.log_error(
							message=f"Failed to create attendance record for {user_email}: {e!s}",
							title="Attendance Sync Error",
						)
						continue

				# Update progress after each batch
				progress = 10 + ((processed_count / len(details)) * 85)
				frappe.publish_progress(
					percent=progress,
					title="Creating Attendance Records",
					description=f"Processed {processed_count} of {len(details)} attendees...",
				)

				# Commit batch to avoid long transactions
				frappe.db.commit()

			self.attendance_synced = 1
			self.save()

			frappe.publish_progress(
				percent=100,
				title="Creating Attendance Records",
				description=f"Successfully synced {processed_count} of {len(details)} attendees!",
			)

			if processed_count < len(details):
				frappe.msgprint(
					f"Synced {processed_count} out of {len(details)} attendees. "
					f"{len(details) - processed_count} records had errors and were skipped. "
					"Check the Error Log for details."
				)
			else:
				frappe.msgprint(f"Successfully synced all {processed_count} attendees!")

		except Exception as e:
			frappe.log_error(
				message=f"Attendance sync failed for webinar {self.name}: {e!s}",
				title="Attendance Sync Failed",
			)
			frappe.throw(_(f"Failed to sync attendance: {e!s}"))

	@frappe.whitelist()
	def sync_registrations_from_zoom(self):
		try:
			details = get_webinar_registrant_details(self.name)

			if not details:
				frappe.msgprint(_("No registration records found for this webinar."))
				return

			frappe.publish_progress(
				percent=5,
				title="Creating Registrant Records",
				description=f"Creating records for {len(details)} unique registrants...",
			)

			# Process in batches to avoid memory issues
			batch_size = ATTENDANCE_SYNC_BATCH_SIZE
			processed_count = 0

			for i in range(0, len(details), batch_size):
				batch = details[i : i + batch_size]

				for registrant in batch:
					try:
						if frappe.db.exists(
							"Zoom Session Registration",
							{
								"registrant_id": registrant.get("id"),
								"reference_doctype": self.doctype,
								"reference_name": self.name,
							},
						):
							continue
						doc = frappe.get_doc(
							{
								"doctype": "Zoom Session Registration",
								"registrant_id": registrant.get("id"),
								"join_url": registrant.get("join_url"),
								"reference_doctype": self.doctype,
								"reference_name": self.name,
								"email": registrant.get("email"),
								"first_name": registrant.get("first_name"),
								"last_name": registrant.get("last_name"),
								"docstatus": 1,
								"synced_from_zoom": 1,
							}
						)
						for question in registrant.get("custom_questions"):
							if question.get("title"):
								doc.append(
									"additional_params",
									{
										"key": question.get("title", "N/A"),
										"value": question.get("value", "N/A"),
									},
								)

						if registrant.get("phone"):
							doc.append(
								"additional_params",
								{
									"key": "Phone",
									"value": registrant.get("phone"),
								},
							)

						doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
						processed_count += 1
					except Exception as e:
						frappe.log_error(
							message=f"Failed to create registrant record for {registrant.get('email')}: {e!s}",
							title="Registrant Sync Error",
						)
						continue

				# Update progress after each batch
				progress = 10 + ((processed_count / len(details)) * 85)
				frappe.publish_progress(
					percent=progress,
					title="Creating Registrant Records",
					description=f"Processed {processed_count} of {len(details)} registrants...",
				)

				# Commit batch to avoid long transactions
				frappe.db.commit()

			frappe.publish_progress(
				percent=100,
				title="Creating Registrant Records",
				description=f"Successfully synced {processed_count} of {len(details)} registrants!",
			)

			if processed_count < len(details):
				frappe.msgprint(
					f"Synced {processed_count} out of {len(details)} registrants. "
					f"{len(details) - processed_count} records had errors and were skipped. "
					"Check the Error Log for details."
				)
			else:
				frappe.msgprint(f"Successfully synced all {processed_count} registrants!")

		except Exception as e:
			frappe.log_error(
				message=f"Registrant sync failed for webinar {self.name}: {e!s}",
				title="Registrant Sync Failed",
			)
			frappe.throw(_(f"Failed to sync registrant: {e!s}"))

	@frappe.whitelist()
	def sync_attendance_in_background(self):
		frappe.enqueue_doc(
			self.doctype,
			self.name,
			"sync_attendance",
			queue="long",
		)

	@frappe.whitelist()
	def sync_registrations_in_background(self):
		frappe.enqueue_doc(
			self.doctype,
			self.name,
			"sync_registrations_from_zoom",
			queue="long",
		)


def get_webinar_attendance_details(webinar_id: str, limit: int = ATTENDANCE_SYNC_BATCH_SIZE):
	headers = get_authenticated_headers_for_zoom()
	all_participants = []
	next_page_token = None
	total_records = 0
	page_count = 0
	max_retries = 3

	while True:
		page_count += 1
		retry_count = 0

		# Build URL with pagination parameters
		url = f"{ZOOM_API_BASE_PATH}/past_webinars/{webinar_id}/participants?page_size={limit}"
		if next_page_token:
			url += f"&next_page_token={next_page_token}"

		while retry_count < max_retries:
			try:
				response = requests.get(url, headers=headers, timeout=30)

				if response.status_code == 200:
					data = response.json()
					participants = data.get("participants", [])
					all_participants.extend(participants)

					# Update total records from first page
					if page_count == 1:
						total_records = data.get("total_records", 0)
						# return total_records

					# Check if there are more pages
					next_page_token = data.get("next_page_token")

					# Update progress
					if total_records > 0:
						progress = min(95, (len(all_participants) / total_records) * 100)
						frappe.publish_progress(
							percent=progress,
							title="Fetching Attendance Data",
							description=f"Fetched {len(all_participants)} of {total_records} participants (Page {page_count})...",
						)

					break  # Success, exit retry loop

				elif response.status_code == 429:  # Rate limit
					retry_after = int(response.headers.get("Retry-After", 60))
					time.sleep(retry_after)
					retry_count += 1

				else:
					if retry_count == max_retries - 1:
						frappe.throw(
							f"Failed to fetch webinar attendance details after {max_retries} attempts: {response.text}"
						)
					retry_count += 1
					time.sleep(2**retry_count)  # Exponential backoff

			except requests.exceptions.RequestException as e:
				if retry_count == max_retries - 1:
					frappe.throw(_(f"Network error while fetching attendance details: {e!s}"))
				retry_count += 1
				time.sleep(2**retry_count)

		# If no more pages, break the main loop
		if not next_page_token:
			break

		# Small delay between pages to be respectful to the API
		time.sleep(0.5)

	# process the attendance to sum the duration based on user email
	# since the same user can join multiple times, we will sum their durations
	attendance_summary = {}
	for participant in all_participants:
		email = participant.get("user_email")
		if email:
			if email not in attendance_summary:
				attendance_summary[email] = {
					"name": participant.get("name"),
					"total_duration": 0,
					"registrant_id": participant.get("registrant_id"),
				}
			attendance_summary[email]["total_duration"] += participant.get("duration", 0)

	# Convert the summary to a list of dictionaries
	attendance_details = [
		{
			"user_email": email,
			"name": details["name"],
			"total_duration": details["total_duration"],
			"registrant_id": details["registrant_id"],
		}
		for email, details in attendance_summary.items()
	]
	# Sort by total duration in descending order
	attendance_details.sort(key=lambda x: x["total_duration"], reverse=True)

	frappe.publish_progress(
		percent=100,
		title="Fetching Attendance Data",
		description=f"Successfully processed {len(attendance_details)} unique attendees from {len(all_participants)} total records",
	)

	return attendance_details


def get_webinar_registrant_details(webinar_id: str, limit: int = ATTENDANCE_SYNC_BATCH_SIZE):
	headers = get_authenticated_headers_for_zoom()
	all_registrants = []
	next_page_token = None
	total_records = 0
	page_count = 0
	max_retries = 3

	while True:
		page_count += 1
		retry_count = 0

		# Build URL with pagination parameters
		url = f"{ZOOM_API_BASE_PATH}/webinars/{webinar_id}/registrants?page_size={limit}"
		if next_page_token:
			url += f"&next_page_token={next_page_token}"

		while retry_count < max_retries:
			try:
				response = requests.get(url, headers=headers, timeout=30)

				if response.status_code == 200:
					data = response.json()
					registrants = data.get("registrants", [])
					all_registrants.extend(registrants)
					# Update total records from first page
					if page_count == 1:
						total_records = data.get("total_records", 0)
						# return total_records

					# Check if there are more pages
					next_page_token = data.get("next_page_token")

					# Update progress
					if total_records > 0:
						progress = min(95, (len(all_registrants) / total_records) * 100)
						frappe.publish_progress(
							percent=progress,
							title="Fetching Registrant Data",
							description=f"Fetched {len(all_registrants)} of {total_records} registrants (Page {page_count})...",
						)

					break  # Success, exit retry loop

				elif response.status_code == 429:  # Rate limit
					retry_after = int(response.headers.get("Retry-After", 60))
					time.sleep(retry_after)
					retry_count += 1

				else:
					if retry_count == max_retries - 1:
						frappe.throw(
							f"Failed to fetch webinar attendance details after {max_retries} attempts: {response.text}"
						)
					retry_count += 1
					time.sleep(2**retry_count)  # Exponential backoff

			except requests.exceptions.RequestException as e:
				if retry_count == max_retries - 1:
					frappe.throw(_(f"Network error while fetching attendance details: {e!s}"))
				retry_count += 1
				time.sleep(2**retry_count)

		# If no more pages, break the main loop
		if not next_page_token:
			break

		# Small delay between pages to be respectful to the API
		time.sleep(0.5)

	return all_registrants


@frappe.whitelist()
def import_existing_webinar(webinar_id: str):
	if not webinar_id:
		frappe.throw("Webinar ID is required.")

	# Check if the webinar already exists in the system
	existing = frappe.db.get_value("Zoom Webinar", {"zoom_webinar_id": webinar_id})
	if existing:
		frappe.msgprint(_("Webinar already exists in the system."))
		return existing

	url = f"{ZOOM_API_BASE_PATH}/webinars/{webinar_id}"
	headers = get_authenticated_headers_for_zoom()
	response = requests.get(url, headers=headers)

	if response.status_code == 200:
		data = response.json()
		processed_datetime = convert_utc_to_timezone(
			get_datetime(data.get("start_time")), data.get("timezone") or "Asia/Calcutta"
		)

		webinar = frappe.get_doc(
			{
				"doctype": "Zoom Webinar",
				"title": data.get("topic"),
				"agenda": data.get("agenda") or data.get("topic"),
				"date": processed_datetime.date() if processed_datetime else None,
				"start_time": processed_datetime.time() if processed_datetime else None,
				"timezone": data.get("timezone"),
				"duration": data.get("duration", 60) * 60,  # Convert minutes to seconds
				"zoom_webinar_id": data.get("id"),
				"zoom_link": data.get("join_url"),
			}
		)
		webinar.flags.in_import = True  # Set flag to skip Zoom API update in on_update
		webinar.insert(ignore_permissions=True)
		frappe.msgprint(_("Webinar imported successfully from Zoom."))
	else:
		frappe.throw(_(f"Failed to fetch webinar details: {response.text}"))
