from rest_framework import generics, status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Count, Avg, Q
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend

from apps.products.models import Product
from apps.orders.models import Order
from .models import ProductReview, ReviewImage, ReviewHelpful, ReviewReport
from .serializers import ProductReviewSerializer, ReviewReportSerializer
from apps.notifications.services import NotificationService

class ProductReviewListView(generics.ListAPIView):
    """
    GET /api/products/{id}/reviews/
    Returns rating summary metrics (average rating, total count, star distributions)
    along with paginated approved reviews.
    """
    serializer_class = ProductReviewSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        product_id = self.kwargs.get("pk")
        return (
            ProductReview.objects.filter(product_id=product_id, status=ProductReview.APPROVED)
            .select_related("user", "product", "variant")
            .prefetch_related("images")
        )

    def list(self, request, *args, **kwargs):
        product_id = self.kwargs.get("pk")
        product = get_object_or_404(Product, pk=product_id)
        
        # Check user eligibility
        user_can_review = False
        user_existing_review = None
        if request.user and request.user.is_authenticated:
            existing = ProductReview.objects.filter(product=product, user=request.user).first()
            if existing:
                user_existing_review = ProductReviewSerializer(existing, context={"request": request}).data
                user_can_review = True
            else:
                user_can_review = Order.objects.filter(
                    user=request.user,
                    status=Order.DELIVERED,
                    items__product=product
                ).exists()

        summary = {
            "average_rating": float(product.average_rating),
            "total_reviews": product.total_reviews,
            "rating_distribution": {
                "5": product.rating_5_count,
                "4": product.rating_4_count,
                "3": product.rating_3_count,
                "2": product.rating_2_count,
                "1": product.rating_1_count,
            },
            "user_can_review": user_can_review,
            "user_existing_review": user_existing_review
        }

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            paginated_response = self.get_paginated_response(serializer.data)
            paginated_response.data["summary"] = summary
            paginated_response.data["success"] = True
            return paginated_response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "success": True,
            "summary": summary,
            "results": serializer.data
        })


class ReviewCreateUpdateDeleteView(APIView):
    """
    POST /api/reviews/ -> Create review (or raise validation if duplicate).
    PUT /api/reviews/{pk}/ -> Update review (must own).
    DELETE /api/reviews/{pk}/ -> Soft delete (set status to hidden/deleted).
    """
    def get_permissions(self):
        if self.request.method in ["POST", "PUT", "DELETE"]:
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    @transaction.atomic
    def post(self, request):
        serializer = ProductReviewSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            review = serializer.save()
            
            # Save uploaded images if any
            images = request.FILES.getlist("images")
            for img in images:
                ReviewImage.objects.create(review=review, image=img)

            # Notification to Customer
            NotificationService.create(
                title="Review Submitted Successfully",
                message=f"Thank you! Your review for '{review.product.name}' was submitted successfully.",
                notification_type="REVIEW",
                priority="LOW",
                reference_type="ProductReview",
                reference_id=str(review.id)
            )

            # Notification to Admin
            NotificationService.create(
                title="New Review Submitted",
                message=f"User {request.user.username} submitted a new review for '{review.product.name}'.",
                notification_type="REVIEW",
                priority="MEDIUM",
                reference_type="ProductReview",
                reference_id=str(review.id)
            )

            return Response({
                "success": True,
                "message": "Review submitted successfully.",
                "review": ProductReviewSerializer(review, context={"request": request}).data
            }, status=status.HTTP_201_CREATED)
        return Response({
            "success": False,
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    @transaction.atomic
    def put(self, request, pk):
        review = get_object_or_404(ProductReview, pk=pk, user=request.user)
        serializer = ProductReviewSerializer(review, data=request.data, partial=True, context={"request": request})
        if serializer.is_valid():
            updated_review = serializer.save()

            # Handle adding new images
            images = request.FILES.getlist("images")
            for img in images:
                ReviewImage.objects.create(review=updated_review, image=img)

            # Trigger recalculation of stats as review contents or rating might have changed
            from .models import recalculate_product_ratings
            recalculate_product_ratings(updated_review.product_id)

            return Response({
                "success": True,
                "message": "Review updated successfully.",
                "review": ProductReviewSerializer(updated_review, context={"request": request}).data
            })
        return Response({
            "success": False,
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    @transaction.atomic
    def delete(self, request, pk):
        # Soft delete: update status to HIDDEN
        review = get_object_or_404(ProductReview, pk=pk)
        
        # Verify ownership (unless staff/admin)
        if review.user != request.user and not request.user.is_staff:
            return Response({
                "success": False,
                "message": "You do not have permission to delete this review."
            }, status=status.HTTP_403_FORBIDDEN)

        review.status = ProductReview.HIDDEN
        review.save(update_fields=["status", "updated_at"])
        
        return Response({
            "success": True,
            "message": "Review deleted successfully (soft delete)."
        })


class ReviewHelpfulToggleView(APIView):
    """
    POST /api/reviews/{id}/helpful/
    Toggles the helpful vote. Updates helpful_count on the review.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        review = get_object_or_404(ProductReview, pk=pk)
        vote_qs = ReviewHelpful.objects.filter(review=review, user=request.user)
        
        if vote_qs.exists():
            # User already voted helpful, so toggle / remove vote
            vote_qs.delete()
            review.helpful_count = max(0, review.helpful_count - 1)
            review.save(update_fields=["helpful_count"])
            voted = False
        else:
            # Add vote
            ReviewHelpful.objects.create(review=review, user=request.user)
            review.helpful_count += 1
            review.save(update_fields=["helpful_count"])
            voted = True

        return Response({
            "success": True,
            "helpful_count": review.helpful_count,
            "has_voted_helpful": voted
        })


class ReviewReportView(APIView):
    """
    POST /api/reviews/{id}/report/
    Submit report against a review.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        review = get_object_or_404(ProductReview, pk=pk)
        data = request.data.copy()
        data["review"] = review.id
        
        serializer = ReviewReportSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Review reported successfully."
            }, status=status.HTTP_201_CREATED)
        return Response({
            "success": False,
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class MyReviewsListView(generics.ListAPIView):
    """
    GET /api/reviews/my-reviews/
    Returns list of reviews written by the authenticated user.
    """
    serializer_class = ProductReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            ProductReview.objects.filter(user=self.request.user)
            .select_related("product", "variant")
            .prefetch_related("images")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "success": True,
            "results": serializer.data
        })


# ===========================================================================
# ADMIN MODERATION & ANALYTICS VIEWS
# ===========================================================================

class AdminReviewListView(generics.ListAPIView):
    """
    GET /api/admin/reviews/
    Admin endpoint to view, search, and filter all reviews.
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = ProductReviewSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    filterset_fields = ["rating", "status", "is_verified_purchase"]
    search_fields = ["user__username", "user__email", "product__name", "title"]
    ordering_fields = ["created_at", "helpful_count", "rating"]

    def get_queryset(self):
        return (
            ProductReview.objects.all()
            .select_related("user", "product", "variant")
            .prefetch_related("images", "reports")
        )


class AdminReviewModerateView(APIView):
    """
    PATCH /api/admin/reviews/{id}/moderate/
    Approve, reject, or hide review.
    """
    permission_classes = [permissions.IsAdminUser]

    @transaction.atomic
    def patch(self, request, pk):
        review = get_object_or_404(ProductReview, pk=pk)
        new_status = request.data.get("status")
        
        if new_status not in [ProductReview.APPROVED, ProductReview.REJECTED, ProductReview.HIDDEN, ProductReview.PENDING]:
            return Response({
                "success": False,
                "message": "Invalid status."
            }, status=status.HTTP_400_BAD_REQUEST)

        review.status = new_status
        review.save(update_fields=["status", "updated_at"])

        return Response({
            "success": True,
            "message": f"Review status updated to {new_status}.",
            "review": ProductReviewSerializer(review, context={"request": request}).data
        })


class AdminReviewBulkModerateView(APIView):
    """
    POST /api/admin/reviews/bulk-moderate/
    """
    permission_classes = [permissions.IsAdminUser]

    @transaction.atomic
    def post(self, request):
        ids = request.data.get("ids", [])
        new_status = request.data.get("status")

        if new_status not in [ProductReview.APPROVED, ProductReview.REJECTED, ProductReview.HIDDEN, ProductReview.PENDING]:
            return Response({
                "success": False,
                "message": "Invalid status."
            }, status=status.HTTP_400_BAD_REQUEST)

        reviews = ProductReview.objects.filter(id__in=ids)
        for review in reviews:
            review.status = new_status
            review.save(update_fields=["status", "updated_at"])

        return Response({
            "success": True,
            "message": f"Successfully updated {reviews.count()} reviews to status '{new_status}'."
        })


class AdminReviewAnalyticsView(APIView):
    """
    GET /api/admin/reviews/analytics/
    Dashboard widget stats.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        total = ProductReview.objects.count()
        avg_rating = ProductReview.objects.filter(status=ProductReview.APPROVED).aggregate(Avg('rating'))['rating__avg'] or 0.00
        pending = ProductReview.objects.filter(status=ProductReview.PENDING).count()
        hidden = ProductReview.objects.filter(status=ProductReview.HIDDEN).count()
        reported = ReviewReport.objects.filter(status=ReviewReport.PENDING).values('review').distinct().count()

        # Growth data (last 30 days count by date)
        from django.db.models.functions import TruncDate
        growth_qs = (
            ProductReview.objects.filter(status=ProductReview.APPROVED)
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')[:30]
        )
        growth_chart = [{"date": str(x['date']), "count": x['count']} for x in growth_qs]

        # Top reviewed products
        top_reviewed = (
            ProductReview.objects.filter(status=ProductReview.APPROVED)
            .values('product__id', 'product__name')
            .annotate(count=Count('id'), avg=Avg('rating'))
            .order_by('-count')[:5]
        )
        top_reviewed_list = [
            {"product_id": x['product__id'], "name": x['product__name'], "count": x['count'], "average_rating": x['avg']}
            for x in top_reviewed
        ]

        return Response({
            "success": True,
            "analytics": {
                "total_reviews": total,
                "average_rating": float(avg_rating),
                "pending_reviews": pending,
                "hidden_reviews": hidden,
                "reported_reviews": reported,
                "top_reviewed_products": top_reviewed_list,
                "review_growth_chart": growth_chart
            }
        })
