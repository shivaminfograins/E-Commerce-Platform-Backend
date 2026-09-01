import logging
from django.utils.timezone import now
from .models import Notification

logger = logging.getLogger(__name__)

from django.db import transaction, IntegrityError

class NotificationService:
    @staticmethod
    def create(
        title,
        message,
        notification_type,
        user=None,
        event_key=None,
        priority="MEDIUM",
        reference_type=None,
        reference_id=None,
        action_url=None,
        icon=None,
    ):
        """
        Creates and saves a notification to the database with event_key idempotency.
        """
        # 0. Idempotency Check
        if user and event_key:
            existing = Notification.objects.filter(user=user, event_key=event_key).first()
            if existing:
                return existing

        try:
            # 1. Save to Database
            notification = Notification.objects.create(
                user=user,
                event_key=event_key,
                title=title,
                message=message,
                notification_type=notification_type,
                priority=priority,
                reference_type=reference_type,
                reference_id=reference_id,
                action_url=action_url,
                icon=icon,
            )

            # 2. Future-Ready integrations (Hooks for Channels/Push/Email)
            NotificationService._trigger_realtime_ws(notification)
            NotificationService._trigger_email_notification(notification)
            NotificationService._trigger_push_notification(notification)

            return notification
        except IntegrityError:
            # Concurrent insert duplicate event_key fallback
            if user and event_key:
                return Notification.objects.filter(user=user, event_key=event_key).first()
            return None
        except Exception as e:
            logger.error(f"Failed to create notification: {str(e)}")
            return None

    @staticmethod
    def create_notification(
        user,
        notification_type,
        title,
        message,
        event_key=None,
        reference_type=None,
        reference_id=None,
        action_url=None,
        icon=None,
        priority="MEDIUM",
    ):
        return NotificationService.create(
            user=user,
            event_key=event_key,
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            reference_type=reference_type,
            reference_id=reference_id,
            action_url=action_url,
            icon=icon,
        )

    @staticmethod
    def create_notification_on_commit(
        user,
        notification_type,
        title,
        message,
        event_key=None,
        reference_type=None,
        reference_id=None,
        action_url=None,
        icon=None,
        priority="MEDIUM",
    ):
        """
        Schedules notification creation to execute after current database transaction commits.
        """
        def _commit_callback():
            NotificationService.create(
                user=user,
                event_key=event_key,
                title=title,
                message=message,
                notification_type=notification_type,
                priority=priority,
                reference_type=reference_type,
                reference_id=reference_id,
                action_url=action_url,
                icon=icon,
            )

        transaction.on_commit(_commit_callback)

    @staticmethod
    def _trigger_realtime_ws(notification):
        """
        Placeholder / Hook for real-time WebSocket delivery using Django Channels.
        """
        # Example channel layer broadcast logic goes here:
        # channel_layer = get_channel_layer()
        # async_to_sync(channel_layer.group_send)("admin_notifications", { ... })
        pass

    @staticmethod
    def _trigger_email_notification(notification):
        """
        Placeholder / Hook for email dispatching if priority is high or criteria met.
        """
        # Example SMTP / SendGrid logic goes here:
        # if notification.priority == Notification.HIGH:
        #     send_mail(notification.title, notification.message, ...)
        pass

    @staticmethod
    def _trigger_push_notification(notification):
        """
        Placeholder / Hook for Web Push / Firebase Cloud Messaging (FCM).
        """
        # Example FCM token messaging goes here:
        # send_fcm_message(notification)
        pass
