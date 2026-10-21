from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.shipping.models import Shipment
from apps.shipping.services import ShippingService


class Command(BaseCommand):
    help = "Periodic fallback sync for active non-terminal shipments to poll tracking status from provider."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=6,
            help="Sync active shipments that have not been synced in the last N hours.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum number of active shipments to process in one run.",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        limit = options["limit"]
        threshold = timezone.now() - timezone.timedelta(hours=hours)

        active_statuses = [
            Shipment.AWB_ASSIGNED,
            Shipment.PICKUP_REQUESTED,
            Shipment.PICKED_UP,
            Shipment.IN_TRANSIT,
            Shipment.OUT_FOR_DELIVERY,
        ]

        active_shipments = Shipment.objects.filter(
            status__in=active_statuses,
            updated_at__lte=threshold,
        ).exclude(awb_code="").order_by("updated_at")[:limit]

        self.stdout.write(f"Found {len(active_shipments)} active shipments for tracking sync...")

        synced_count = 0
        error_count = 0

        for shipment in active_shipments:
            try:
                self.stdout.write(f"Syncing tracking for Shipment ID {shipment.id} (AWB: {shipment.awb_code})...")
                ShippingService.track_shipment(shipment)
                synced_count += 1
            except Exception as exc:
                self.stderr.write(f"Failed to sync shipment {shipment.id}: {exc}")
                error_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Tracking sync complete. Successfully synced: {synced_count}, Errors: {error_count}.")
        )
