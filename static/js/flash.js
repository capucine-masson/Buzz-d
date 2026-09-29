(function () {
    "use strict";

    const FLASH_QUERY_PARAMS = ["error", "loaded"];
    const DISPLAY_DURATION_MS = 4000;

    // Une fois affiché, on retire error/loaded de l'URL pour qu'un simple
    // rechargement (ou le reload déclenché par le WebSocket) ne fasse pas
    // ressurgir un message obsolète.
    function stripFlashParamsFromUrl() {
        const url = new URL(window.location.href);
        let changed = false;
        FLASH_QUERY_PARAMS.forEach((key) => {
            if (url.searchParams.has(key)) {
                url.searchParams.delete(key);
                changed = true;
            }
        });
        if (changed) {
            window.history.replaceState({}, "", url.pathname + url.search);
        }
    }

    function scheduleAutoDismiss() {
        document.querySelectorAll(".note--flash").forEach((el) => {
            setTimeout(() => {
                el.classList.add("note--fade-out");
                el.addEventListener("transitionend", () => el.remove(), { once: true });
            }, DISPLAY_DURATION_MS);
        });
    }

    scheduleAutoDismiss();
    stripFlashParamsFromUrl();
})();
