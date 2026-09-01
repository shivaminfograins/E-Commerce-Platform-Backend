from django.core.management.base import BaseCommand
from apps.orders.services import expire_unpaid_orders


class Command(BaseCommand):
    help = "Expire unpaid online orders older than expiration threshold and release reserved stock"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=30,
            help="Expiration threshold in minutes (default: 30)",
        )

    def handle(self, *args, **options):
        minutes = options["minutes"]
        self.stdout.write(f"Scanning for unpaid online orders older than {minutes} minutes...")
        expired_count = expire_unpaid_orders(expiration_minutes=minutes)
        self.stdout.write(self.style.SUCCESS(f"Successfully expired {expired_count} unpaid order(s)."))
