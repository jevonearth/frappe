# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe

from frappe.utils import getdate, cint, add_months, date_diff, add_days, nowdate
from frappe.core.doctype.user.user import STANDARD_USERS
from frappe.model.document import Document

weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

class Event(Document):
	def validate(self):
		if self.starts_on and self.ends_on and self.starts_on > self.ends_on:
			frappe.msgprint(frappe._("Event end must be after start"), raise_exception=True)

def get_permission_query_conditions():
	return """(tabEvent.event_type='Public' or tabEvent.owner='%(user)s'
		or exists(select * from `tabEvent User` where
			`tabEvent User`.parent=tabEvent.name and `tabEvent User`.person='%(user)s')
		or exists(select * from `tabEvent Role` where
			`tabEvent Role`.parent=tabEvent.name
			and `tabEvent Role`.role in ('%(roles)s')))
		""" % {
			"user": frappe.session.user,
			"roles": "', '".join(frappe.get_roles(frappe.session.user))
		}

def has_permission(doc, user):
	if doc.event_type=="Public" or doc.owner==user:
		return True

	if doc.get("event_individuals", {"person": user}):
		return True

	if doc.get("event_roles", {"role":("in", frappe.get_roles(user))}):
		return True

	return False


def send_event_digest():
	today = nowdate()
	for user in frappe.db.sql("""select name, email, language
		from tabUser where ifnull(enabled,0)=1
		and user_type='System User' and name not in ({})""".format(", ".join(["%s"]*len(STANDARD_USERS))),
		STANDARD_USERS, as_dict=1):

		events = get_events(today, today, user.name, for_reminder=True)
		if events:
			text = ""
			frappe.set_user_lang(user.name, user.language)

			text = "<h3>" + frappe._("Events In Today's Calendar") + "</h3>"
			for e in events:
				if e.all_day:
					e.starts_on = "All Day"
				text += "<h4>%(starts_on)s: %(subject)s</h4><p>%(description)s</p>" % e

			text += '<p style="color: #888; font-size: 80%; margin-top: 20px; padding-top: 10px; border-top: 1px solid #eee;">'\
				+ frappe._("Daily Event Digest is sent for Calendar Events where reminders are set.")+'</p>'

			from frappe.utils.email_lib import sendmail
			sendmail(recipients=user.email, subject=frappe._("Upcoming Events for Today"),
				msg = text)

@frappe.whitelist()
def get_events(start, end, user=None, for_reminder=False):
	if not user:
		user = frappe.session.user
	roles = frappe.get_roles(user)
	events = frappe.db.sql("""select name, subject, description,
		starts_on, ends_on, owner, all_day, event_type, repeat_this_event, repeat_on,
		monday, tuesday, wednesday, thursday, friday, saturday, sunday
		from tabEvent where ((
			(date(starts_on) between date('%(start)s') and date('%(end)s'))
			or (date(ends_on) between date('%(start)s') and date('%(end)s'))
			or (date(starts_on) <= date('%(start)s') and date(ends_on) >= date('%(end)s'))
		) or (
			date(starts_on) <= date('%(start)s') and ifnull(repeat_this_event,0)=1 and
			ifnull(repeat_till, "3000-01-01") > date('%(start)s')
		))
		%(reminder_condition)s
		and (event_type='Public' or owner='%(user)s'
		or exists(select * from `tabEvent User` where
			`tabEvent User`.parent=tabEvent.name and person='%(user)s')
		or exists(select * from `tabEvent Role` where
			`tabEvent Role`.parent=tabEvent.name
			and `tabEvent Role`.role in ('%(roles)s')))
		order by starts_on""" % {
			"start": start,
			"end": end,
			"reminder_condition": "and ifnull(send_reminder,0)=1" if for_reminder else "",
			"user": user,
			"roles": "', '".join(roles)
		}, as_dict=1)

	# process recurring events
	start = start.split(" ")[0]
	end = end.split(" ")[0]
	add_events = []
	remove_events = []

	def add_event(e, date):
		new_event = e.copy()
		new_event.starts_on = date + " " + e.starts_on.split(" ")[1]
		if e.ends_on:
			new_event.ends_on = date + " " + e.ends_on.split(" ")[1]
		add_events.append(new_event)

	for e in events:
		if e.repeat_this_event:
			event_start, time_str = e.starts_on.split(" ")
			if e.repeat_on=="Every Year":
				start_year = cint(start.split("-")[0])
				end_year = cint(end.split("-")[0])
				event_start = "-".join(event_start.split("-")[1:])

				# repeat for all years in period
				for year in range(start_year, end_year+1):
					date = str(year) + "-" + event_start
					if date >= start and date <= end:
						add_event(e, date)

				remove_events.append(e)

			if e.repeat_on=="Every Month":
				date = start.split("-")[0] + "-" + start.split("-")[1] + "-" + event_start.split("-")[2]

				# last day of month issue, start from prev month!
				try:
					getdate(date)
				except ValueError:
					date = date.split("-")
					date = date[0] + "-" + str(cint(date[1]) - 1) + "-" + date[2]

				start_from = date
				for i in xrange(int(date_diff(end, start) / 30) + 3):
					if date >= start and date <= end and date >= event_start:
						add_event(e, date)
					date = add_months(start_from, i+1)

				remove_events.append(e)

			if e.repeat_on=="Every Week":
				weekday = getdate(event_start).weekday()
				# monday is 0
				start_weekday = getdate(start).weekday()

				# start from nearest weeday after last monday
				date = add_days(start, weekday - start_weekday)

				for cnt in xrange(int(date_diff(end, start) / 7) + 3):
					if date >= start and date <= end and date >= event_start:
						add_event(e, date)

					date = add_days(date, 7)

				remove_events.append(e)

			if e.repeat_on=="Every Day":
				for cnt in xrange(date_diff(end, start) + 1):
					date = add_days(start, cnt)
					if date >= event_start and date <= end \
						and e[weekdays[getdate(date).weekday()]]:
						add_event(e, date)
				remove_events.append(e)

	for e in remove_events:
		events.remove(e)

	events = events + add_events

	for e in events:
		# remove weekday properties (to reduce message size)
		for w in weekdays:
			del e[w]

	return events
