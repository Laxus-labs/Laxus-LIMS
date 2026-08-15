from django.contrib.auth import get_user_model

from apps.customers.models import Customer

User = get_user_model()


def make_user(username="frontdesk", role="analyst"):
    return User.objects.create_user(username=username, password="pw", role=role)


def make_customer(user, code="CUST-1", name="Acme Pharma", **kwargs):
    return Customer.objects.create(code=code, name=name, created_by=user, **kwargs)
