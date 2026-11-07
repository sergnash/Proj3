/**
 * NoteMaster — плавающий фон с полупрозрачными кругами.
 * Создаёт круги разных размеров и цветов, медленно всплывающих снизу вверх.
 * Не перекрывает основной контент (pointer-events: none, opacity ≤ 0.08).
 */

(function () {
    var COLORS = ["--accent-lemon", "--accent-orange", "--accent-lime"];
    var MAX_CIRCLES = 15;
    var MIN_SIZE = 40;
    var MAX_SIZE = 180;
    var MIN_DURATION = 15;
    var MAX_DURATION = 35;

    function createCircle() {
        var canvas = document.getElementById("background-canvas");
        if (!canvas) return;

        var circle = document.createElement("div");
        circle.classList.add("floating-circle");
        circle.classList.add(
            "floating-circle--" +
                COLORS[Math.floor(Math.random() * COLORS.length)]
                    .replace("--accent-", "")
        );

        var size = MIN_SIZE + Math.random() * (MAX_SIZE - MIN_SIZE);
        var left = Math.random() * 100;
        var duration = MIN_DURATION + Math.random() * (MAX_DURATION - MIN_DURATION);
        var delay = Math.random() * 10;

        circle.style.width = size + "px";
        circle.style.height = size + "px";
        circle.style.left = left + "%";
        circle.style.animationDuration = duration + "s";
        circle.style.animationDelay = delay + "s";

        canvas.appendChild(circle);

        // Удаляем круг после завершения анимации, чтобы не накапливать DOM
        setTimeout(function () {
            if (circle.parentElement) {
                circle.parentElement.removeChild(circle);
            }
        }, (duration + delay) * 1000);
    }

    // Начальный набор кругов
    for (var i = 0; i < MAX_CIRCLES; i++) {
        createCircle();
    }

    // Периодически добавляем новые круги
    setInterval(function () {
        var canvas = document.getElementById("background-canvas");
        if (canvas && canvas.children.length < MAX_CIRCLES) {
            createCircle();
        }
    }, 3000);
})();
