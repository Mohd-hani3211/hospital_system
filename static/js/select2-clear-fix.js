(function(window, document, $) {
  "use strict";

  if (!$) {
    return;
  }

  var SELECT2_CDN_URL = "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js";
  var DEFAULT_PLACEHOLDER = "الرجاء الاختيار";

  function booleanDataValue(value, fallback) {
    if (value === undefined || value === null || value === "") {
      return fallback;
    }
    if (value === true || value === "true" || value === "1" || value === 1) {
      return true;
    }
    if (value === false || value === "false" || value === "0" || value === 0) {
      return false;
    }
    return fallback;
  }

  function optionText($option) {
    return $.trim($option.text() || "");
  }

  function getSelectPlaceholder($select) {
    var dataPlaceholder = $select.attr("data-placeholder") || $select.attr("placeholder");
    if (dataPlaceholder) {
      return dataPlaceholder;
    }

    var $blankOption = $select.find("option[value='']").first();
    if ($blankOption.length && optionText($blankOption)) {
      return optionText($blankOption);
    }

    var $disabledSelected = $select.find("option[disabled]:selected").first();
    if ($disabledSelected.length && optionText($disabledSelected)) {
      return optionText($disabledSelected);
    }

    return DEFAULT_PLACEHOLDER;
  }

  function hasEmptyOption($select) {
    return $select.find("option").filter(function() {
      return (this.value || "") === "";
    }).length > 0;
  }

  function shouldAllowClear($select) {
    var explicit = booleanDataValue($select.attr("data-allow-clear"), null);
    if (explicit !== null) {
      return explicit;
    }

    if ($select.prop("multiple")) {
      return !$select.prop("required");
    }

    return !$select.prop("required") || hasEmptyOption($select);
  }

  function prepareClearableSelect($select, allowClear, placeholder) {
    if (!allowClear || $select.prop("multiple")) {
      return;
    }

    if (!hasEmptyOption($select)) {
      $select.prepend($("<option>", { value: "", text: "" }));
    }

    $select.attr("data-placeholder", placeholder);
  }

  function initializeSelect2(context) {
    if (!$.fn.select2) {
      return;
    }

    $("select", context || document).each(function() {
      var $select = $(this);

      if ($select.hasClass("select2-hidden-accessible")) {
        return;
      }

      if (booleanDataValue($select.attr("data-select2"), true) === false) {
        return;
      }

      var placeholder = getSelectPlaceholder($select);
      var allowClear = shouldAllowClear($select);
      var $parentModal = $select.closest(".modal");
      var options = {
        dir: "rtl",
        width: "100%",
        allowClear: allowClear,
        placeholder: placeholder
      };

      prepareClearableSelect($select, allowClear, placeholder);

      if ($parentModal.length) {
        options.dropdownParent = $parentModal;
      }

      $select.select2(options);
    });
  }

  function resolveSelectFromClearButton(clearButton) {
    var container = clearButton.closest(".select2-container");
    if (!container) {
      return null;
    }

    var previous = container.previousElementSibling;
    if (previous && previous.matches && previous.matches("select")) {
      return previous;
    }

    return $(container).prevAll("select.select2-hidden-accessible").first().get(0) || null;
  }

  function clearSelect2Selection(event) {
    var clearButton = event.target.closest && event.target.closest(".select2-selection__clear");
    if (!clearButton) {
      return;
    }

    var select = resolveSelectFromClearButton(clearButton);
    if (!select) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    var $select = $(select);
    if ($select.prop("multiple")) {
      $select.val([]);
    } else {
      $select.val("");
    }
    $select.trigger("change");
    if ($select.data("select2")) {
      $select.select2("close");
    }
    $select.trigger("select2:clear");
  }

  document.addEventListener("mousedown", clearSelect2Selection, true);
  document.addEventListener("touchstart", clearSelect2Selection, true);

  function loadSelect2(callback) {
    if ($.fn.select2) {
      callback();
      return;
    }

    $.getScript(SELECT2_CDN_URL, callback);
  }

  window.initializeHospitalSelect2 = initializeSelect2;

  $(function() {
    loadSelect2(function() {
      initializeSelect2(document);
    });
  });
})(window, document, window.jQuery);
