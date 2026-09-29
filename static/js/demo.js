(function () {
    "use strict";

    const grid = document.getElementById("phone-grid");
    const addButton = document.getElementById("phone-add-button");
    if (!grid || !addButton) {
        return;
    }

    const MAX_PHONES = 4;

    function phoneCount() {
        return grid.querySelectorAll(".phone-frame").length;
    }

    function updateAddButton() {
        addButton.hidden = phoneCount() >= MAX_PHONES;
    }

    addButton.addEventListener("click", () => {
        if (phoneCount() >= MAX_PHONES) {
            return;
        }
        const frame = document.createElement("div");
        frame.className = "phone-frame";
        frame.innerHTML =
            '<div class="phone-frame__notch" aria-hidden="true"></div>' +
            '<iframe class="phone-frame__screen" src="/?embedded=1" loading="lazy" title="Nouvel écran"></iframe>';
        grid.insertBefore(frame, addButton);
        updateAddButton();
    });

    updateAddButton();
})();
