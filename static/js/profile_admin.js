// document.addEventListener('DOMContentLoaded', function() {
//     console.log("1. تم تحميل سكريبت الجافاسكريبت الصافي بنجاح!");

//     // البحث عن الحقول باستخدام JavaScript الصافية
//     const roleSelect = document.querySelector('#role');
//     const deptRow = document.querySelector('.field-managing_department');
//     const specialtyRow = document.querySelector('.field-specialty');

//     function toggleFields() {
//         if (!roleSelect) return; // إذا لم يجد الحقل يوقف التنفيذ لتجنب الأخطاء
        
//         // الحصول على النص المختار من القائمة المنسدلة
//         const selectedText = roleSelect.options[roleSelect.selectedIndex].text;
//         console.log("2. الدور المختار حالياً هو:", selectedText);

//         if (selectedText.includes('3')) {
//             // حالة رئيس قسم
//             if (deptRow) deptRow.style.display = 'block';
//             if (specialtyRow) specialtyRow.style.display = 'none';
//         } else if (selectedText.includes('4')) {
//             // حالة فني
//             if (deptRow) deptRow.style.display = 'none';
//             if (specialtyRow) specialtyRow.style.display = 'block';
//         } else {
//             // إخفاء الكل للحالات الأخرى
//             if (deptRow) deptRow.style.display = 'none';
//             if (specialtyRow) specialtyRow.style.display = 'none';
//         }
//     }

//     if (roleSelect) {
//         // ربط دالة التغيير بالحدث "change"
//         roleSelect.addEventListener('change', toggleFields);
        
//         // تشغيل الدالة فوراً عند تحميل الصفحة
//         toggleFields();
//     } else {
//         console.error("خطأ: لم يتم العثور على حقل الدور الوظيفي في الصفحة.");
//     }
// });


document.addEventListener('DOMContentLoaded', function() {
    const roleSelect = document.querySelector('#id_job_title');
    const deptRow = document.querySelector('#row_id_managing_department');
    const specialtyRow = document.querySelector('#row_id_specialty');

    // قراءة البيانات من وسم الـ script الذي أنشأه الجانغو
    const permissionsElement = document.getElementById('job-permissions-data');
    let jobPermissions = {};
    
    if (permissionsElement) {
        jobPermissions = JSON.parse(permissionsElement.textContent);
    }

    function toggleFields() {
        if (!roleSelect) return;
        console.log(roleSelect)

        const selectedJobId = roleSelect.value;
        
        // إذا لم يتم اختيار أي مسمى أو القائمة فارغة
        console.log(jobPermissions[selectedJobId]+ 'and the value is: '+selectedJobId);
        if (!selectedJobId || !jobPermissions[selectedJobId]) {
            if (deptRow) deptRow.style.display = 'none';
            if (specialtyRow) specialtyRow.style.display = 'none';
            return;
        }

        // جلب الصلاحيات الخاصة بالـ id المختار مباشرة من الكائن
        const perms = jobPermissions[selectedJobId];
        const isManager = perms.is_manager === true;
        const isEngineer = perms.is_engineer === true;

        if (isManager) {
            if (deptRow) deptRow.style.display = 'block';
            if (specialtyRow) specialtyRow.style.display = 'none';
        } else if (isEngineer) {
            if (deptRow) deptRow.style.display = 'none';
            if (specialtyRow) specialtyRow.style.display = 'block';
        } else {
            if (deptRow) deptRow.style.display = 'none';
            if (specialtyRow) specialtyRow.style.display = 'none';
        }
    }

    if (roleSelect) {
        $(roleSelect).on('change', toggleFields);
        toggleFields(); // تشغيل أولي لضبط حالة الحقول عند تحميل الصفحة
    }
});