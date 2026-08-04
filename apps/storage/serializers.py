from rest_framework import serializers

from .models import StorageLocation


class StorageLocationSerializer(serializers.ModelSerializer):
    path = serializers.CharField(read_only=True)
    capacity = serializers.IntegerField(read_only=True)

    class Meta:
        model = StorageLocation
        fields = [
            "id",
            "code",
            "name",
            "location_type",
            "parent",
            "path",
            "grid_rows",
            "grid_cols",
            "temperature_c",
            "capacity",
            "is_active",
            "notes",
        ]
