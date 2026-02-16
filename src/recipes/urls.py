"""Frontend URL routes for recipes."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.recipe_list, name="recipe_list"),
    path("available/", views.available_recipes, name="available_recipes"),
    path("<slug:slug>/", views.recipe_detail, name="recipe_detail"),
]
