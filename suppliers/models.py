from django.db import models


class Supplier(models.Model):
    name = models.CharField(max_length=200)
    reference = models.CharField(max_length=60, unique=True)
    email = models.EmailField(blank=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.reference} - {self.name}"
