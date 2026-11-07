/**
 * NoteMaster — минимальная интерактивность UI.
 * Подтверждение удаления, сворачивание формы, список выбранных файлов,
 * авто-скрытие flash-сообщений.
 */

(function () {
    // Подтверждение удаления
    window.confirmDelete = function (noteId, title) {
        if (confirm("Удалить заметку «" + title + "»?")) {
            var form = document.getElementById("delete-form-" + noteId);
            if (form) form.submit();
        }
    };

    // Раскрытие/скрытие формы добавления
    window.toggleAddForm = function () {
        var form = document.getElementById("add-form");
        if (!form) return;
        var willOpen = !form.classList.contains("form-section--open");
        form.classList.toggle("form-section--open", willOpen);
        if (willOpen) {
            form.scrollIntoView({ behavior: "smooth", block: "nearest" });
            var input = form.querySelector("input[name='title']");
            if (input) input.focus();
        }
    };

    // Показ имён выбранных файлов (вместо нативного текста input)
    function renderFileChips(control) {
        var list = document.getElementById(control.dataset.fileList);
        if (!list) return;
        list.innerHTML = "";
        Array.prototype.forEach.call(control.files, function (file) {
            var chip = document.createElement("li");
            chip.className = "file-input__chip";
            chip.textContent = file.name + " (" + Math.max(1, Math.round(file.size / 1024)) + " КБ)";
            list.appendChild(chip);
        });
    }

    document.querySelectorAll(".file-input__control").forEach(function (control) {
        control.addEventListener("change", function () {
            renderFileChips(control);
        });
    });

    // Авто-скрытие flash-сообщений
    document.querySelectorAll(".flash").forEach(function (el) {
        setTimeout(function () {
            el.style.transition = "opacity 0.3s, transform 0.3s";
            el.style.opacity = "0";
            el.style.transform = "translateX(100%)";
            setTimeout(function () {
                if (el.parentElement) el.parentElement.removeChild(el);
            }, 300);
        }, 4000);
    });
})();
