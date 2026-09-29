(function () {
    "use strict";

    const main = document.querySelector(".page--room");
    if (!main) {
        return;
    }

    const roomCode = main.dataset.roomCode;
    const playersList = document.getElementById("players-list");

    function renderPlayers(players) {
        if (!playersList) {
            return;
        }
        playersList.textContent = "";
        players.forEach((p) => {
            const li = document.createElement("li");
            li.textContent = `${p.nickname} — ${p.score} pt(s)`;
            playersList.appendChild(li);
        });
    }

    function connect() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${protocol}://${window.location.host}/ws/${roomCode}`);

        ws.addEventListener("message", (event) => {
            const message = JSON.parse(event.data);
            if (message.type === "players_update") {
                renderPlayers(message.players);
            } else if (message.type === "playlist_loaded") {
                // Navigue vers une URL propre plutôt qu'un reload() brut : celui-ci
                // réutiliserait l'URL courante telle quelle, qui peut encore porter
                // un ?error=... périmé d'un essai précédent (course avec la
                // redirection du formulaire d'import).
                const url = new URL(window.location.href);
                url.searchParams.delete("error");
                url.searchParams.set("loaded", message.track_count);
                window.location.href = url.pathname + url.search;
            }
        });

        ws.addEventListener("close", () => {
            setTimeout(connect, 1500);
        });
    }

    connect();
})();
