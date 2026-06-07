from django.db import models
from django.contrib.auth.models import User

class Building(models.Model):
    name = models.CharField(max_length=100, verbose_name="اسم المبنى") # المبنى الرئيسي، مركز العيون، المسجد..
    description = models.CharField(max_length=100,null=True,blank=True, verbose_name="وصف المبنى") # المبنى الرئيسي، مركز العيون، المسجد..
    
    
    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "المبنى"
        verbose_name_plural = "1. المباني"


class Floor(models.Model):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='floors', verbose_name="المبنى")
    name = models.CharField(max_length=50, verbose_name="اسم الطابق") # الطابق الأول، الثاني، البدروم..
    description = models.CharField(max_length=100,null=True,blank=True, verbose_name="وصف الطابق") # المبنى الرئيسي، مركز العيون، المسجد..
    def __str__(self):
        return f"{self.building.name} - {self.name}"
    class Meta:
        verbose_name = "الطابق"
        verbose_name_plural = "2. الطوابق"



class Department(models.Model):
    floor = models.ForeignKey(Floor, on_delete=models.CASCADE, related_name='departments', verbose_name="الطابق")
    name = models.CharField(max_length=100, verbose_name="اسم القسم") # عناية مركزة، رقود باطنة، مختبر..

    # حقول إحصائية اختيارية بناءً على وصفك (يمكن تركها فارغة)
    rooms_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="عدد الغرف")
    beds_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="سعة الأسرة")
    bathrooms_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="عدد الحمامات")

    def __str__(self):
        return f"{self.name} - {self.floor.name} - {self.floor.building.name} "
    class Meta:
        verbose_name = "القسم"
        verbose_name_plural = "4. الأقسام والمرافق"