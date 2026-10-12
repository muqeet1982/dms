"""Seed the shared city list (Pakistan) used by the customer and supplier forms."""

from django.core.management.base import BaseCommand

from apps.core.models import City

# name, province — districts/tehsils that distributors actually deliver to
PAKISTAN = [
    ("Karachi", "Sindh"), ("Hyderabad", "Sindh"), ("Sukkur", "Sindh"), ("Larkana", "Sindh"), ("Nawabshah", "Sindh"), ("Mirpur Khas", "Sindh"),
    ("Jacobabad", "Sindh"), ("Shikarpur", "Sindh"), ("Khairpur", "Sindh"), ("Dadu", "Sindh"), ("Thatta", "Sindh"), ("Badin", "Sindh"), ("Tando Adam", "Sindh"),
    ("Tando Allahyar", "Sindh"), ("Ghotki", "Sindh"), ("Umerkot", "Sindh"), ("Sanghar", "Sindh"), ("Matiari", "Sindh"), ("Kandhkot", "Sindh"),
    ("Lahore", "Punjab"), ("Faisalabad", "Punjab"), ("Rawalpindi", "Punjab"), ("Multan", "Punjab"), ("Gujranwala", "Punjab"), ("Sialkot", "Punjab"),
    ("Bahawalpur", "Punjab"), ("Sargodha", "Punjab"), ("Sheikhupura", "Punjab"), ("Jhang", "Punjab"), ("Rahim Yar Khan", "Punjab"), ("Gujrat", "Punjab"),
    ("Kasur", "Punjab"), ("Okara", "Punjab"), ("Sahiwal", "Punjab"), ("Wah Cantonment", "Punjab"), ("Dera Ghazi Khan", "Punjab"), ("Chiniot", "Punjab"),
    ("Kamoke", "Punjab"), ("Hafizabad", "Punjab"), ("Mandi Bahauddin", "Punjab"), ("Jhelum", "Punjab"), ("Khanewal", "Punjab"), ("Muzaffargarh", "Punjab"),
    ("Vehari", "Punjab"), ("Bahawalnagar", "Punjab"), ("Attock", "Punjab"), ("Chakwal", "Punjab"), ("Mianwali", "Punjab"), ("Bhakkar", "Punjab"),
    ("Layyah", "Punjab"), ("Rajanpur", "Punjab"), ("Lodhran", "Punjab"), ("Pakpattan", "Punjab"), ("Toba Tek Singh", "Punjab"), ("Narowal", "Punjab"),
    ("Nankana Sahib", "Punjab"), ("Daska", "Punjab"), ("Gojra", "Punjab"), ("Jaranwala", "Punjab"), ("Burewala", "Punjab"), ("Kot Addu", "Punjab"),
    ("Murree", "Punjab"), ("Taxila", "Punjab"), ("Pattoki", "Punjab"), ("Shorkot", "Punjab"), ("Khushab", "Punjab"), ("Kharian", "Punjab"),
    ("Islamabad", "Islamabad Capital Territory"),
    ("Peshawar", "Khyber Pakhtunkhwa"), ("Mardan", "Khyber Pakhtunkhwa"), ("Abbottabad", "Khyber Pakhtunkhwa"), ("Mingora", "Khyber Pakhtunkhwa"),
    ("Kohat", "Khyber Pakhtunkhwa"), ("Dera Ismail Khan", "Khyber Pakhtunkhwa"), ("Nowshera", "Khyber Pakhtunkhwa"), ("Swabi", "Khyber Pakhtunkhwa"),
    ("Charsadda", "Khyber Pakhtunkhwa"), ("Mansehra", "Khyber Pakhtunkhwa"), ("Haripur", "Khyber Pakhtunkhwa"), ("Bannu", "Khyber Pakhtunkhwa"),
    ("Swat", "Khyber Pakhtunkhwa"), ("Chitral", "Khyber Pakhtunkhwa"), ("Timergara", "Khyber Pakhtunkhwa"), ("Batkhela", "Khyber Pakhtunkhwa"),
    ("Quetta", "Balochistan"), ("Turbat", "Balochistan"), ("Khuzdar", "Balochistan"), ("Hub", "Balochistan"), ("Chaman", "Balochistan"),
    ("Gwadar", "Balochistan"), ("Sibi", "Balochistan"), ("Zhob", "Balochistan"), ("Loralai", "Balochistan"), ("Dera Murad Jamali", "Balochistan"),
    ("Muzaffarabad", "Azad Kashmir"), ("Mirpur", "Azad Kashmir"), ("Kotli", "Azad Kashmir"), ("Rawalakot", "Azad Kashmir"), ("Bhimber", "Azad Kashmir"),
    ("Gilgit", "Gilgit-Baltistan"), ("Skardu", "Gilgit-Baltistan"), ("Hunza", "Gilgit-Baltistan"), ("Chilas", "Gilgit-Baltistan"),
]


class Command(BaseCommand):
    help = "Seed the shared city list (idempotent)."

    def handle(self, *args, **options):
        created = 0
        for name, province in PAKISTAN:
            _, made = City.objects.get_or_create(name=name, province=province, country="Pakistan")
            created += int(made)
        self.stdout.write(self.style.SUCCESS(f"Cities: {City.objects.count()} total, {created} added"))
