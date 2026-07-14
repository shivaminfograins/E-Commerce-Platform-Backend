from django.db import models


class AppSetting(models.Model):
    """
    Simple key-value database table to store application settings.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()

    def __str__(self):
        return self.key
