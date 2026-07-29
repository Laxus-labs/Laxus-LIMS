from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    """
    Roles exist to enforce segregation of duties, which is the whole point
    of a regulated lab's permission model: the analyst who produces a
    result must not be the person who approves it. Anything that relaxes
    that is a finding in an audit, so the check lives in the domain layer
    (see apps.testing.services) rather than in a view decorator someone
    can forget to apply.
    """

    ANALYST = "analyst", "Analyst"
    REVIEWER = "reviewer", "Reviewer"
    QA_APPROVER = "qa_approver", "QA approver"
    LAB_MANAGER = "lab_manager", "Lab manager"
    ADMIN = "admin", "Administrator"


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ANALYST)

    # Regulated labs need to know which human a record belongs to even after
    # someone leaves, so accounts are deactivated, never deleted.
    employee_id = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "app_user"
        constraints = [
            models.UniqueConstraint(
                fields=["employee_id"],
                condition=~models.Q(employee_id=""),
                name="uniq_employee_id_when_set",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    # --- capability checks ------------------------------------------------
    # Expressed as capabilities rather than role comparisons scattered
    # through the codebase, so adding a role later is a one-line change.

    @property
    def can_review_results(self) -> bool:
        return self.role in {Role.REVIEWER, Role.QA_APPROVER, Role.LAB_MANAGER, Role.ADMIN}

    @property
    def can_approve_results(self) -> bool:
        return self.role in {Role.QA_APPROVER, Role.ADMIN}

    @property
    def can_dispose_samples(self) -> bool:
        return self.role in {Role.LAB_MANAGER, Role.QA_APPROVER, Role.ADMIN}

    @property
    def can_adjust_inventory(self) -> bool:
        """Manual stock adjustments bypass the normal reserve/consume flow,
        so they are restricted and always require a reason code."""
        return self.role in {Role.LAB_MANAGER, Role.ADMIN}
