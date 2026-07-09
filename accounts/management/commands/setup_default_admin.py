from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import JobTitle, JobTitlePermission, Profile


class Command(BaseCommand):
    help = "Create or repair the default system administrator user."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin", help="Admin username. Default: admin")
        parser.add_argument("--password", default="Admin@12345", help="Admin password. Default: Admin@12345")
        parser.add_argument("--first-name", default="مدير", help="Admin first name.")
        parser.add_argument("--last-name", default="النظام", help="Admin last name.")
        parser.add_argument("--email", default="admin@example.com", help="Admin email.")
        parser.add_argument("--phone", default="777000000", help="Admin phone number.")
        parser.add_argument("--employee-id", default="ADMIN-001", help="Admin employee ID.")
        parser.add_argument("--job-title", default="مدير النظام", help="Admin job title.")
        parser.add_argument(
            "--no-reset-password",
            action="store_true",
            help="Do not reset password if the user already exists.",
        )

    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]

        with transaction.atomic():
            job_title, _ = JobTitle.objects.get_or_create(title_name=options["job_title"])
            permissions, _ = JobTitlePermission.objects.get_or_create(job_title=job_title)

            for field in JobTitlePermission._meta.fields:
                if field.get_internal_type() == "BooleanField":
                    setattr(permissions, field.name, True)

            # These are role-type flags, and the model does not allow both together.
            permissions.is_engineer = False
            permissions.is_department_manager = False
            permissions.save()

            user, created = User.objects.get_or_create(username=username)
            user.first_name = options["first_name"]
            user.last_name = options["last_name"]
            user.email = options["email"]
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            if created or not options["no_reset_password"]:
                user.set_password(password)
            user.save()

            employee_id = self._unique_employee_id(options["employee_id"], user)
            profile, _ = Profile.objects.get_or_create(
                user=user,
                defaults={
                    "job_title": job_title,
                    "phone_number": options["phone"],
                    "employee_id": employee_id,
                },
            )
            profile.job_title = job_title
            profile.phone_number = options["phone"] or profile.phone_number or "777000000"
            if not profile.employee_id:
                profile.employee_id = employee_id
            profile.save()

        self.stdout.write(self.style.SUCCESS("Default admin user is ready."))
        self.stdout.write(f"Username: {username}")
        if created or not options["no_reset_password"]:
            self.stdout.write(f"Password: {password}")
        else:
            self.stdout.write("Password was not changed.")

    def _unique_employee_id(self, requested_employee_id, user):
        requested_employee_id = requested_employee_id or "ADMIN-001"
        existing = Profile.objects.filter(employee_id=requested_employee_id).exclude(user=user).exists()
        if not existing:
            return requested_employee_id

        counter = 1
        while True:
            candidate = f"{requested_employee_id}-{counter}"
            if not Profile.objects.filter(employee_id=candidate).exclude(user=user).exists():
                return candidate
            counter += 1
