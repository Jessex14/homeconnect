import uuid

from django.db import models

from apps.organizations.models import Property


class Building(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("property", "name"), name="uniq_building_name_per_property")]
        ordering = ("name",)


class Unit(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Maintenance"
        INACTIVE = "inactive", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.PROTECT, related_name="units")
    code = models.CharField(max_length=80)
    label = models.CharField(max_length=180, blank=True)
    unit_type = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("property", "code"), name="uniq_unit_code_per_property")]
        indexes = [models.Index(fields=("property", "status"))]
        ordering = ("code",)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.building_id and self.property_id and self.building.property_id != self.property_id:
            raise ValidationError({"building": "Building must belong to the same property as the unit."})


class RentalListing(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        PAUSED = "paused", "Paused"

    unit = models.OneToOneField(Unit, on_delete=models.PROTECT, related_name="rental_listing")
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    location_label = models.CharField(max_length=140, blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(monthly_rent__gt=0), name="rental_listing_rent_positive")]
        indexes = [models.Index(fields=("status", "monthly_rent"))]
        ordering = ("-published_at", "-created_at")

    def __str__(self):
        return self.title


class RentalListingPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(RentalListing, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="rental-listings/%Y/%m/")
    alt_text = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "created_at")
