"""公開前台的網址設定。"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("license/", views.data_license, name="license"),
    path("stats/", views.public_stats_view, name="public_stats"),
    path("admin-stats/", views.staff_stats_view, name="staff_stats"),
    path("go-home/", views.go_home, name="go_home"),
    path("go-home/logout/", views.go_home_logout, name="go_home_logout"),
    path("backup/", views.backup_database, name="backup_database"),
    path("restore/", views.restore_database, name="restore_database"),
    path("full-export/", views.full_export_page, name="full_export"),
    path(
        "full-export/download.xlsx",
        views.full_export_download,
        name="full_export_download",
    ),
    path("species/", views.public_species_list, name="public_species_list"),
    path(
        "species/export.csv",
        views.public_species_export,
        name="public_species_export",
    ),
    path(
        "species/<int:pk>/",
        views.public_species_detail,
        name="public_species_detail",
    ),
]
