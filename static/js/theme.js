(function () {
    const STORAGE_KEY = "buzzd-theme";
    const root = document.documentElement;
    const button = document.getElementById("theme-toggle");
    const icon = document.getElementById("theme-toggle-icon");

    const MOON_PATH = "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z";
    const SUN_PATHS = [
        "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z",
        "M12 1v3", "M12 20v3", "M4.22 4.22l2.12 2.12", "M17.66 17.66l2.12 2.12",
        "M1 12h3", "M20 12h3", "M4.22 19.78l2.12-2.12", "M17.66 6.34l2.12-2.12"
    ];

    function setIconPaths(paths) {
        if (!icon) return;
        icon.innerHTML = paths.map(function (d) { return '<path d="' + d + '"></path>'; }).join("");
    }

    function applyTheme(theme) {
        root.setAttribute("data-theme", theme);
        setIconPaths(theme === "dark" ? SUN_PATHS : [MOON_PATH]);
    }

    let stored = null;
    try {
        stored = localStorage.getItem(STORAGE_KEY);
    } catch (e) {}

    const preferred = stored || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    applyTheme(preferred);

    if (button) {
        button.addEventListener("click", function () {
            const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
            applyTheme(next);
            try {
                localStorage.setItem(STORAGE_KEY, next);
            } catch (e) {}
        });
    }
})();
