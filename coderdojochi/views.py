from django.conf import settings
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext, Context
from django.template.loader import get_template
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.core.mail import EmailMessage
from django.core.urlresolvers import reverse
from django.contrib import messages, auth
from django.contrib.auth import get_user_model
from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django import forms

from coderdojochi.models import Mentor, Guardian, Student, Course, Session, Order, Meeting, Donation
from coderdojochi.forms import MentorForm, GuardianForm, StudentForm

from calendar import HTMLCalendar
from datetime import date, datetime, timedelta
from itertools import groupby

from django.utils.html import conditional_escape as esc
from django.utils.safestring import mark_safe

from django.utils.translation import ugettext_lazy as _

import arrow

# this will assign User to our custom CDCUser
User = get_user_model()

from registration.backends.simple.views import RegistrationView
from registration import forms as registration_forms
from registration import signals

import calendar


class CDCRegistrationForm(registration_forms.RegistrationForm):

    first_name = forms.CharField()
    last_name = forms.CharField()
    username = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_email(self):
        """
        Validate that the username is alphanumeric and is not already
        in use.

        """
        existing = User.objects.filter(username__iexact=self.cleaned_data['email'])
        if existing.exists():
            raise forms.ValidationError(_("A user with that email already exists."))
        else:
            return self.cleaned_data['email']

    def clean(self):
        """
        Verifiy that the values entered into the two password fields
        match. Note that an error here will end up in
        ``non_field_errors()`` because it doesn't apply to a single
        field.

        """
        if 'password1' in self.cleaned_data and 'password2' in self.cleaned_data:
            if self.cleaned_data['password1'] != self.cleaned_data['password2']:
                raise forms.ValidationError(_("The two password fields didn't match."))

        return self.cleaned_data


class RegisterView(RegistrationView):

    form_class = CDCRegistrationForm

    def register(self, request, **cleaned_data):

        email, password, first_name, last_name = cleaned_data['email'], cleaned_data['password1'], cleaned_data['first_name'], cleaned_data['last_name']
        username = email

        user = User.objects.create_user(username, email, password, first_name=first_name, last_name=last_name)

        new_user = authenticate(username=username, password=password)
        login(request, new_user)
        signals.user_registered.send(sender=self.__class__,
                                     user=new_user,
                                     request=request)
        return new_user

    def get_success_url(self, request, user):

        url = user.get_absolute_url()

        if request.GET.get('next'):
            url += '?next=' + request.GET.get('next')

        return (url, (), {})

def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month / 12
    month = month % 12 + 1
    day = min(sourcedate.day,calendar.monthrange(year,month)[1])

    return date(year,month,day)

def home(request, template_name="home.html"):

    upcoming_classes = Session.objects.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')[:3]

    return render_to_response(template_name, {
        'upcoming_classes': upcoming_classes
    }, context_instance=RequestContext(request))

@login_required
def welcome(request, template_name="welcome.html"):

    user = request.user
    account = False
    add_student = False
    students = False
    form = False
    role = user.role if user.role else False

    if request.method == 'POST':

        next = False

        if request.GET.get('next'):
            next = request.GET.get('next')

        if role:
            if role == 'mentor':
                form = MentorForm(request.POST, instance=get_object_or_404(Mentor, user=user))
            else:
                account = get_object_or_404(Guardian, user=user)
                if not account.phone:
                    form = GuardianForm(request.POST, instance=account)
                else:
                    form = StudentForm(request.POST)
                    if form.is_valid():
                        new_student = form.save(commit=False)
                        new_student.guardian = account
                        new_student.save()
                        messages.add_message(request, messages.SUCCESS, 'Student Registered.')

                    if next:
                        return HttpResponseRedirect(next)
                    else:
                        return HttpResponseRedirect(reverse('welcome'))

            if form.is_valid():
                form.save()
                # if role == 'mentor' or account.get_students().count():
                messages.add_message(request, messages.SUCCESS, 'Profile information saved.')

                if next:
                    return HttpResponseRedirect(next)
                else:
                    return HttpResponseRedirect(reverse('dojo'))
            else:
                messages.add_message(request, messages.ERROR, 'There was an error. Please try again.')
                return HttpResponseRedirect(reverse('welcome'))
        else:
            if request.POST.get('role') == 'mentor':
                role = 'mentor'
                mentor = Mentor(user=user)
                mentor.first_name = user.first_name
                mentor.last_name = user.last_name
                mentor.save()
                user.role = role
            else:
                role = 'guardian'
                guardian = Guardian(user=user)
                guardian.first_name = user.first_name
                guardian.last_name = user.last_name
                guardian.save()
                user.role = role


            merge_vars = {
                'user': request.user.username,
                'first_name': request.user.first_name,
                'last_name': request.user.last_name
            }

            if role == 'mentor':

                # check for next upcoming meeting
                next_meeting = Meeting.objects.filter(active=True).order_by('start_date').first()

                if next_meeting:
                    merge_vars['next_intro_meeting_url'] = next_meeting.get_absolute_url()

                sendSystemEmail(request, 'Welcome!', 'coderdojochi-welcome-mentor', merge_vars)
            else:
                # check for next upcoming class
                next_class = Session.objects.filter(active=True).order_by('start_date').first()

                if next_class:
                    merge_vars['next_class_url'] = next_class.get_absolute_url()

                sendSystemEmail(request, 'Welcome!', 'coderdojochi-welcome-guardian', merge_vars)

            user.save()

            if next:
                next = '?next=' + next
            else:
                next = ''

            return HttpResponseRedirect(reverse('welcome') + next)

    if role:
        if role == 'mentor':
            mentor = get_object_or_404(Mentor, user=user)
            account = mentor
            form = MentorForm(instance=account)

        if role == 'guardian':
            guardian = get_object_or_404(Guardian, user=user)
            account = guardian
            if not account.phone:
                form = GuardianForm(instance=account)
            else:
                add_student = True
                form = StudentForm(initial={'guardian': guardian.pk})

    if account and account.first_name:
        if role == 'mentor':
            return HttpResponseRedirect(reverse('dojo'))
        else:
            students = account.get_students() if account.get_students().count() else False

    return render_to_response(template_name, {
        'role': role,
        'account': account,
        'form': form,
        'add_student': add_student,
        'students': students,
    }, context_instance=RequestContext(request))


def sessions(request, year=False, month=False, template_name="sessions.html"):

    now = datetime.now()

    year = int(year) if year else now.year
    month = int(month) if month else now.month

    calendar_date = date(day=1, month=month, year=year)
    prev_date = add_months(calendar_date,-1)
    next_date = add_months(calendar_date,1)

    all_sessions = Session.objects.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')
    sessions = all_sessions.filter(start_date__year=year, start_date__month=month).order_by('start_date')
    cal = SessionsCalendar(sessions).formatmonth(year, month)

    return render_to_response(template_name,{
        'all_sessions': all_sessions,
        'sessions': sessions,
        'calendar': mark_safe(cal),
        'calendar_date': calendar_date,
        'prev_date': prev_date,
        'next_date': next_date
    }, context_instance=RequestContext(request))


def session_detail(request, year, month, day, slug, session_id, template_name="session-detail.html"):
    session_obj = get_object_or_404(Session, id=session_id)


    mentor_signed_up = False
    is_guardian = False
    account = False
    students = False

    if request.method == 'POST':
        if request.POST['waitlist']:

            if request.POST['waitlist'] == 'student':
                student = Student.objects.get(id=int(request.POST['account_id']))

                if request.POST['remove'] == 'true':
                    session_obj.waitlist_students.remove(student)
                    session_obj.save()
                    messages.add_message(request, messages.SUCCESS, 'You have been removed from the waitlist. Thanks for letting us know.')
                else:
                    session_obj.waitlist_students.add(student)
                    session_obj.save()
                    messages.add_message(request, messages.SUCCESS, 'Added to waitlist successfully.')
            else:
                mentor = Mentor.objects.get(id=int(request.POST['account_id']))

                if request.POST['remove'] == 'true':
                    session_obj.waitlist_mentors.remove(mentor)
                    session_obj.save()
                    messages.add_message(request, messages.SUCCESS, 'You have been removed from the waitlist. Thanks for letting us know.')
                else:
                    session_obj.waitlist_mentors.add(mentor)
                    session_obj.save()
                    messages.add_message(request, messages.SUCCESS, 'Added to waitlist successfully.')
        else:
            messages.add_message(request, messages.ERROR, 'Invalid request, please try again.')

        return HttpResponseRedirect(reverse('session_detail', args=(session_obj.start_date.year, session_obj.start_date.month, session_obj.start_date.day, session_obj.course.slug, session_obj.id)))

    upcoming_classes = Session.objects.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')

    if request.user.is_authenticated():

        if not request.user.role:
            messages.add_message(request, messages.WARNING, 'Please select one of the following options to continue.')
            return HttpResponseRedirect(reverse('welcome') + '?next=' + session_obj.get_absolute_url())

        if request.user.role == 'mentor':
            mentor = get_object_or_404(Mentor, user=request.user)
            account = mentor
            mentor_signed_up = True if mentor in session_obj.mentors.all() else False
            spots_remaining = ( session_obj.capacity / 2 ) - session_obj.mentors.all().count()
        else:
            guardian = get_object_or_404(Guardian, user=request.user)
            account = guardian
            is_guardian = True
            students = guardian.get_students() if guardian.get_students().count() else False
            spots_remaining = session_obj.capacity - session_obj.get_current_students().all().count()
    else:
        spots_remaining = session_obj.capacity - session_obj.get_current_students().all().count()

    return render_to_response(template_name,{
        'session': session_obj,
        'mentor_signed_up': mentor_signed_up,
        'is_guardian': is_guardian,
        'students': students,
        'account': account,
        'upcoming_classes': upcoming_classes,
        'spots_remaining': spots_remaining
    }, context_instance=RequestContext(request))


@login_required
def session_sign_up(request, year, month, day, slug, session_id, student_id=False, template_name="session-sign-up.html"):

    session_obj = get_object_or_404(Session, id=session_id)
    student = False
    guardian = False

    if not request.user.role:
        messages.add_message(request, messages.WARNING, 'Please select one of the following options to continue.')
        return HttpResponseRedirect(reverse('welcome') + '?next=' + session_obj.get_absolute_url())

    if request.user.role == 'mentor':
        mentor = get_object_or_404(Mentor, user=request.user)
        user_signed_up = True if mentor in session_obj.mentors.all() else False
    else:
        student = get_object_or_404(Student, id=student_id)
        guardian = get_object_or_404(Guardian, user=request.user)
        user_signed_up = True if student.is_registered_for_session(session_obj) else False

    undo = False

    if request.method == 'POST':

        if request.user.role == 'mentor':
            if user_signed_up:
                session_obj.mentors.remove(mentor)
                undo = True
            else:
                session_obj.mentors.add(mentor)
        else:
            if user_signed_up:
                order = get_object_or_404(Order, student=student, session=session_obj)
                order.delete()
                undo = True
            else:
                ip = request.META['REMOTE_ADDR']
                order = Order.objects.get_or_create(guardian=guardian, student=student, session=session_obj, ip=ip)

        session_obj.save()

        if undo:
            messages.add_message(request, messages.SUCCESS, 'Thanks for letting us know!')
        else:
            messages.add_message(request, messages.SUCCESS, 'Success! See you there!')


            if request.user.role == 'mentor':

                sendSystemEmail(request, 'Upcoming class confirmation', 'coderdojochi-class-confirm-mentor', {
                    'first_name': request.user.first_name,
                    'last_name': request.user.last_name,
                    'class_code': session_obj.course.code,
                    'class_title': session_obj.course.title,
                    'class_description': session_obj.course.description,
                    'class_start_date': arrow.get(session_obj.start_date).format('dddd, MMMM D, YYYY'),
                    'class_start_time': arrow.get(session_obj.start_date).format('h:mm a'),
                    'class_end_date': arrow.get(session_obj.end_date).format('dddd, MMMM D, YYYY'),
                    'class_end_time': arrow.get(session_obj.end_date).format('h:mm a'),
                    'class_location_name': session_obj.location.name,
                    'class_location_address': session_obj.location.address,
                    'class_location_address2': session_obj.location.address2,
                    'class_location_city': session_obj.location.city,
                    'class_location_state': session_obj.location.state,
                    'class_location_zip': session_obj.location.zip,
                    'class_additional_info': session_obj.additional_info,
                    'class_url': session_obj.get_absolute_url()
                })

            else:

                sendSystemEmail(request, 'Upcoming class confirmation', 'coderdojochi-class-confirm-guardian', {
                    'first_name': request.user.first_name,
                    'last_name': request.user.last_name,
                    'student_first_name': student.first_name,
                    'student_last_name': student.last_name,
                    'class_code': session_obj.course.code,
                    'class_title': session_obj.course.title,
                    'class_description': session_obj.course.description,
                    'class_start_date': arrow.get(session_obj.start_date).format('dddd, MMMM D, YYYY'),
                    'class_start_time': arrow.get(session_obj.start_date).format('h:mm a'),
                    'class_end_date': arrow.get(session_obj.end_date).format('dddd, MMMM D, YYYY'),
                    'class_end_time': arrow.get(session_obj.end_date).format('h:mm a'),
                    'class_location_name': session_obj.location.name,
                    'class_location_address': session_obj.location.address,
                    'class_location_address2': session_obj.location.address2,
                    'class_location_city': session_obj.location.city,
                    'class_location_state': session_obj.location.state,
                    'class_location_zip': session_obj.location.zip,
                    'class_additional_info': session_obj.additional_info,
                    'class_url': session_obj.get_absolute_url()
                })

        return HttpResponseRedirect(reverse('session_detail', args=(session_obj.start_date.year, session_obj.start_date.month, session_obj.start_date.day, session_obj.course.slug, session_obj.id)))

    return render_to_response(template_name,{
        'session': session_obj,
        'user_signed_up': user_signed_up,
        'student': student
    }, context_instance=RequestContext(request))


def meeting_detail(request, year, month, day, meeting_id, template_name="meeting-detail.html"):
    meeting_obj = get_object_or_404(Meeting, id=meeting_id)

    mentor_signed_up = False

    if request.user.is_authenticated():
        mentor = get_object_or_404(Mentor, user=request.user)
        mentor_signed_up = True if mentor in meeting_obj.mentors.all() else False

    return render_to_response(template_name,{
        'meeting': meeting_obj,
        'mentor_signed_up': mentor_signed_up,
    }, context_instance=RequestContext(request))


@login_required
def meeting_sign_up(request, year, month, day, meeting_id, student_id=False, template_name="meeting-sign-up.html"):

    meeting_obj = get_object_or_404(Meeting, id=meeting_id)

    mentor = get_object_or_404(Mentor, user=request.user)
    user_signed_up = True if mentor in meeting_obj.mentors.all() else False

    undo = False

    if request.method == 'POST':

        if user_signed_up:
            meeting_obj.mentors.remove(mentor)
            undo = True
        else:
            meeting_obj.mentors.add(mentor)

        meeting_obj.save()

        if undo:
            messages.add_message(request, messages.SUCCESS, 'Thanks for letting us know!')
        else:
            messages.add_message(request, messages.SUCCESS, 'Success! See you there!')

            sendSystemEmail(request, 'Upcoming mentor meeting confirmation', 'coderdojochi-meeting-confirm-mentor', {
                'first_name': request.user.first_name,
                'last_name': request.user.last_name,
                'meeting_title': meeting_obj.meeting_type.title,
                'meeting_description': meeting_obj.meeting_type.description,
                'meeting_start_date': arrow.get(meeting_obj.start_date).format('dddd, MMMM D, YYYY'),
                'meeting_start_time': arrow.get(meeting_obj.start_date).format('h:mm a'),
                'meeting_end_date': arrow.get(meeting_obj.end_date).format('dddd, MMMM D, YYYY'),
                'meeting_end_time': arrow.get(meeting_obj.end_date).format('h:mm a'),
                'meeting_location_name': meeting_obj.location.name,
                'meeting_location_address': meeting_obj.location.address,
                'meeting_location_address2': meeting_obj.location.address2,
                'meeting_location_city': meeting_obj.location.city,
                'meeting_location_state': meeting_obj.location.state,
                'meeting_location_zip': meeting_obj.location.zip,
                'meeting_additional_info': meeting_obj.additional_info,
                'meeting_url': meeting_obj.get_absolute_url()
            })

        return HttpResponseRedirect(reverse('meeting_detail', args=(meeting_obj.start_date.year, meeting_obj.start_date.month, meeting_obj.start_date.day, meeting_obj.id)))

    return render_to_response(template_name,{
        'meeting': meeting_obj,
        'user_signed_up': user_signed_up
    }, context_instance=RequestContext(request))

def volunteer(request, template_name="volunteer.html"):

    return render_to_response(template_name,{}, context_instance=RequestContext(request))

def faqs(request, template_name="faqs.html"):

    return render_to_response(template_name,{}, context_instance=RequestContext(request))

@login_required
def dojo(request, template_name="dojo.html"):

    context = {
        'user': request.user
    }

    if request.user.role:

        if request.user.role == 'mentor':
            mentor = get_object_or_404(Mentor, user=request.user)
            account = mentor

            mentor_sessions = Session.objects.filter(mentors=mentor)
            upcoming_sessions = mentor_sessions.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')
            past_sessions = mentor_sessions.filter(active=True, end_date__lte=datetime.now()).order_by('start_date')

            upcoming_meetings = Meeting.objects.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')


            if request.method == 'POST':
                form = MentorForm(request.POST, instance=account)
                if form.is_valid():
                    form.save()
                    messages.add_message(request, messages.SUCCESS, 'Profile information saved.')
                    return HttpResponseRedirect(reverse('dojo'))
                else:
                    messages.add_message(request, messages.ERROR, 'There was an error. Please try again.')
            else:
                form = MentorForm(instance=account)

            context['upcoming_sessions'] = upcoming_sessions
            context['upcoming_meetings'] = upcoming_meetings
            context['past_sessions'] = past_sessions

        if request.user.role == 'guardian':
            guardian = get_object_or_404(Guardian, user=request.user)
            account = guardian

            students = Student.objects.filter(guardian=guardian)
            student_orders = Order.objects.filter(student__in=students)
            upcoming_orders = student_orders.filter(active=True, session__end_date__gte=datetime.now()).order_by('session__start_date')
            past_orders = student_orders.filter(active=True, session__end_date__gte=datetime.now()).order_by('session__start_date')

            if request.method == 'POST':
                form = GuardianForm(request.POST, instance=account)
                if form.is_valid():
                    form.save()
                    messages.add_message(request, messages.SUCCESS, 'Profile information saved.')
                    return HttpResponseRedirect(reverse('dojo'))
                else:
                    messages.add_message(request, messages.ERROR, 'There was an error. Please try again.')
            else:
                form = GuardianForm(instance=account)

            context['students'] = students
            context['upcoming_orders'] = upcoming_orders
            context['past_orders'] = past_orders


        context['account'] = account
        context['form'] = form

    else:
        if request.GET.get('next'):
            return HttpResponseRedirect(reverse('welcome') + '?next=' + request.GET.get('next'))
        else:
            messages.add_message(request, messages.WARNING, 'Tell us a little about yourself before going on to your dojo')
            return HttpResponseRedirect(reverse('welcome'))


    return render_to_response(template_name, context, context_instance=RequestContext(request))


class SessionsCalendar(HTMLCalendar):

    def __init__(self, sessions):
        super(SessionsCalendar, self).__init__()
        self.sessions = self.group_by_day(sessions)

    def formatday(self, day, weekday):
        if day != 0:
            cssclass = self.cssclasses[weekday]
            if date.today() == date(self.year, self.month, day):
                cssclass += ' today'
            if day in self.sessions:
                cssclass += ' filled'
                body = []
                for cdc_session in self.sessions[day]:
                    remaining_spots = cdc_session.capacity - cdc_session.get_current_students().all().count()
                    dayclass = 'unavailable' if not remaining_spots else 'available'
                    body.append('<a class="' + dayclass + '" href="%s">' % cdc_session.get_absolute_url())
                    if cdc_session.course.code:
                        body.append(esc(cdc_session.course.code) + ': ')
                    body.append(esc(cdc_session.course.title))
                    body.append('</a>')
                return self.day_cell(cssclass, '%d %s' % (day, ''.join(body)))
            return self.day_cell(cssclass, day)
        return self.day_cell('noday', '&nbsp;')

    def formatmonth(self, year, month):
        self.year, self.month = year, month
        return super(SessionsCalendar, self).formatmonth(year, month)

    def group_by_day(self, sessions):
        field = lambda cdc_session: cdc_session.start_date.day
        return dict(
            [(day, list(items)) for day, items in groupby(sessions, field)]
        )

    def day_cell(self, cssclass, body):
        return '<td class="%s">%s</td>' % (cssclass, body)


def mentors(request, template_name="mentors.html"):

    mentors = Mentor.objects.filter(active=True)

    return render_to_response(template_name, {
        'mentors': mentors
    }, context_instance=RequestContext(request))


def mentor_detail(request, mentor_id=False, template_name="mentor-detail.html"):

    mentor = get_object_or_404(Mentor, id=mentor_id)

    return render_to_response(template_name, {
        'mentor': mentor
    }, context_instance=RequestContext(request))


@login_required
def student_detail(request, student_id=False, template_name="student-detail.html"):

    access = True

    if request.user.role == 'guardian' and student_id:
        student = get_object_or_404(Student, id=student_id)
        guardian = get_object_or_404(Guardian, user=request.user)

        if not student.guardian == guardian:
            access = False

        form = StudentForm(instance=student)
    else:
        access = False


    if not access:
        return HttpResponseRedirect(reverse('dojo'))
        messages.add_message(request, messages.ERROR, 'You do not have permissions to edit this student.')


    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.add_message(request, messages.SUCCESS, 'Student Updated.')
            return HttpResponseRedirect(reverse('dojo'))

    return render_to_response(template_name, {
        'form': form
    }, context_instance=RequestContext(request))

def donate(request, template_name="donate.html"):

    item_number = False

    if request.method == 'POST':

        # if paypal notification response
        if 'item_number' in request.POST:
            verifyDonation(request.POST['item_number'])
            return HttpResponse('success')

        # if new donation form submit
        if 'first_name' in request.POST and 'last_name' in request.POST and 'email' in request.POST and 'amount' in request.POST:
            donation = Donation(first_name=request.POST['first_name'], last_name=request.POST['last_name'], email=request.POST['email'], amount=request.POST['amount'])
            donation.save()

            return HttpResponse(donation.id)
        else:
            return HttpResponse('fail')

    # if paypal notification response
    if 'item_number' in request.GET:
        verifyDonation(request.GET['item_number'])

    return render_to_response(template_name,{}, context_instance=RequestContext(request))

def verifyDonation(donation_id):
    donation = get_object_or_404(Donation, id=donation_id)
    donation.verified = True;

    if not donation.receipt_sent:
        sendSystemEmail(request, 'Thank you!', 'coderdojochi-donation-receipt', {
            'first_name': donation.first_name,
            'last_name': donation.last_name,
            'email': donation.email,
            'amount': donation.amount
        })
        donation.receipt_sent = True

    donation.save()

def about(request, template_name="about.html"):

    return render_to_response(template_name,{}, context_instance=RequestContext(request))


def privacy(request, template_name="privacy.html"):

    return render_to_response(template_name,{}, context_instance=RequestContext(request))



@login_required
def cdc_admin(request, template_name="cdc-admin.html"):

    if not request.user.is_staff:
        messages.add_message(request, messages.ERROR, 'You do not have permission to access this page.')
        return HttpResponseRedirect(reverse('sessions'))

    sessions = Session.objects.all()

    upcoming_sessions = sessions.filter(active=True, end_date__gte=datetime.now()).order_by('start_date')
    past_sessions = sessions.filter(active=True, end_date__lte=datetime.now()).order_by('start_date')

    return render_to_response(template_name,{
        'upcoming_sessions': upcoming_sessions,
        'past_sessions': past_sessions
    }, context_instance=RequestContext(request))


@login_required
def session_stats(request, session_id, template_name="session-stats.html"):

    if not request.user.is_staff:
        messages.add_message(request, messages.ERROR, 'You do not have permission to access this page.')
        return HttpResponseRedirect(reverse('sessions'))

    session_obj = get_object_or_404(Session, id=session_id)

    current_orders_checked_in = session_obj.get_current_orders(checked_in=True)

    students_checked_in = current_orders_checked_in.values('student')
    attendance_percentage = session_obj.get_current_students().count() /  current_orders_checked_in.count() * 100

    # Genders

    # Average Age
    student_ages = []
    for order in current_orders_checked_in:
        student_ages.append(order.student.get_age())
    average_age = reduce(lambda x, y: x + y, student_ages) / len(student_ages)

    return render_to_response(template_name,{
        'session': session_obj,
        'students_checked_in': students_checked_in,
        'attendance_percentage': attendance_percentage,
        'average_age': average_age
    }, context_instance=RequestContext(request))

@login_required
def session_check_in(request, session_id, template_name="session-check-in.html"):

    if not request.user.is_staff:
        messages.add_message(request, messages.ERROR, 'You do not have permission to access this page.')
        return HttpResponseRedirect(reverse('sessions'))

    session_obj = get_object_or_404(Session, id=session_id)

    if request.method == 'POST':

        if 'order_id' in request.POST:

            order = get_object_or_404(Order, id=request.POST['order_id'])

            if order.check_in:
                order.check_in = None
            else:
                order.check_in = datetime.now()

            if order.guardian.first_name + ' ' + order.guardian.last_name != request.POST['order_alternate_guardian']:
                order.alternate_guardian = request.POST['order_alternate_guardian']

            order.save()
        else:
            messages.add_message(request, messages.ERROR, 'Invalid Order')

    return render_to_response(template_name,{
        'session': session_obj,
    }, context_instance=RequestContext(request))


def sendSystemEmail(request, subject, template_name, merge_vars):

    # add global merge variables
    merge_vars['current_year'] = datetime.now().year
    merge_vars['company'] = 'CoderDojoChi'
    merge_vars['site_url'] = settings.SITE_URL

    msg = EmailMessage(subject=subject, from_email=settings.DEFAULT_FROM_EMAIL,
                       to=[request.user.email])
    msg.template_name = template_name
    msg.global_merge_vars = merge_vars
    msg.inline_css = True
    msg.use_template_subject = True

    # Optional Mandrill-specific extensions:
    # msg.tags = ['one tag', 'two tag', 'red tag', 'blue tag']
    # msg.metadata = {'user_id': '8675309'}

    # Send it:
    msg.send()
