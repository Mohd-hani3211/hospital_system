

 function confirmDelete() {
    Swal.fire({
  title: "هل أنت متأكد من الحذف ؟",
    text: "لن تتمكن من استعادة هذا الموظف بعد الحذف!",
    icon: "warning",
    type: "warning",
  showCancelButton: true,
  
  confirmButtonText: "تاكيد الحذف",
  confrimButtonClass: "btn btn-danger",
  cancelButtonClass: "btn btn-secondary",
  cancelButtonText: `الغاء`,
}).then((result) => {
  /* Read more about isConfirmed, isDenied below */
  if (result.isConfirmed) {
    document.getElementById('delete-form').submit();
    // Swal.fire("Saved!", "", "success");
  }
});
}
