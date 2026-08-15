from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.customers.models import Customer
from apps.samples.models import Sample, SampleState, SampleType
from apps.storage.models import LocationType, StorageLocation

User = get_user_model()


def make_customer(user, code="CUST-1", name="Acme Pharma"):
    return Customer.objects.create(code=code, name=name, created_by=user)


def make_user(username="tech", role="analyst"):
    return User.objects.create_user(username=username, password="pw", role=role)


def make_hierarchy():
    """Facility > Room > Freezer > Box(8x12), returned as a dict."""
    facility = StorageLocation.objects.create(
        code="mumbai_hq", name="Mumbai HQ", location_type=LocationType.FACILITY
    )
    room = StorageLocation.objects.create(
        code="lab_2", name="Molecular Lab 2", location_type=LocationType.ROOM, parent=facility
    )
    freezer = StorageLocation.objects.create(
        code="frz_03",
        name="Freezer 3",
        location_type=LocationType.FREEZER,
        parent=room,
        temperature_c=Decimal("-80.0"),
    )
    box = StorageLocation.objects.create(
        code="box_a1",
        name="Box A1",
        location_type=LocationType.BOX,
        parent=freezer,
        grid_rows=8,
        grid_cols=12,
    )
    return {"facility": facility, "room": room, "freezer": freezer, "box": box}


def make_sample(user, barcode="BC-1", state=SampleState.RECEIVED, volume=1000, **kwargs):
    return Sample.objects.create(
        barcode=barcode,
        sample_type=kwargs.pop("sample_type", SampleType.SERUM),
        state=state,
        volume=Decimal(volume),
        received_at=timezone.now(),
        created_by=user,
        **kwargs,
    )
