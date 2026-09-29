(function () {
    "use strict";

    const grid = document.getElementById("phone-grid");
    if (!grid) {
        return;
    }
    const addButton = document.getElementById("phone-add-button");

    const MAX_PHONES = 8;

    function phoneCount() {
        return grid.querySelectorAll(".phone-frame").length;
    }

    function updateAddButton() {
        if (addButton) {
            addButton.hidden = phoneCount() >= MAX_PHONES;
        }
    }

    function createBlankFrame() {
        const frame = document.createElement("div");
        frame.className = "phone-frame";

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "phone-remove-button";
        removeBtn.setAttribute("aria-label", "Retirer ce téléphone");
        removeBtn.textContent = "×";
        frame.appendChild(removeBtn);

        const label = document.createElement("span");
        label.className = "phone-frame__label";
        label.textContent = "En attente";
        frame.appendChild(label);

        const notch = document.createElement("div");
        notch.className = "phone-frame__notch";
        notch.setAttribute("aria-hidden", "true");
        frame.appendChild(notch);

        const iframe = document.createElement("iframe");
        iframe.className = "phone-frame__screen";
        iframe.src = "/";
        iframe.loading = "lazy";
        iframe.title = "Nouvel écran";
        frame.appendChild(iframe);

        return frame;
    }

    if (addButton) {
        addButton.addEventListener("click", () => {
            if (phoneCount() >= MAX_PHONES) {
                return;
            }
            grid.insertBefore(createBlankFrame(), addButton);
            updateAddButton();
        });
    }

    // Délégation d'événement : fonctionne aussi bien pour les cadrans rendus
    // par le serveur que pour ceux ajoutés dynamiquement ci-dessus.
    grid.addEventListener("click", (event) => {
        const removeBtn = event.target.closest(".phone-remove-button");
        if (!removeBtn) {
            return;
        }
        const frame = removeBtn.closest(".phone-frame");
        if (frame) {
            frame.remove();
            updateAddButton();
        }
    });

    updateAddButton();
})();
