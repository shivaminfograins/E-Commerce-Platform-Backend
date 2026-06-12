from django.urls import path
from .views import (
    ForgotPasswordAPIView,
    RegisterAPIView,
    ProfileAPIView,AddressListCreateAPIView,
    AddressDetailAPIView,
    ResetPasswordAPIView,
)


urlpatterns = [
    path("register/",RegisterAPIView.as_view(),name="register",),
    path("profile/", ProfileAPIView.as_view(),name="profile",),

    path("addresses/",AddressListCreateAPIView.as_view()),
    path("addresses/<int:pk>/",AddressDetailAPIView.as_view()),

    path("forgot-password/",ForgotPasswordAPIView.as_view(),name="forgot-password",),
    path("reset-password/",ResetPasswordAPIView.as_view(),name="reset-password",),
    
]