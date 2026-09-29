(function () {
    "use strict";

    const main = document.querySelector(".page--room");
    if (!main) {
        return;
    }

    const roomCode = main.dataset.roomCode;
    const player = main.dataset.player;
    const isHost = main.dataset.isHost === "1";

    const copyCodeBtn = document.getElementById("copy-code-btn");
    const copyCodeIcon = document.getElementById("copy-code-icon");

    if (copyCodeBtn) {
        copyCodeBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(roomCode);
            } catch (err) {
                return;
            }
            copyCodeBtn.classList.add("is-copied");
            if (copyCodeIcon) {
                copyCodeIcon.innerHTML = '<path d="M20 6 9 17l-5-5"></path>';
            }
            setTimeout(() => {
                copyCodeBtn.classList.remove("is-copied");
                if (copyCodeIcon) {
                    copyCodeIcon.innerHTML = '<rect x="9" y="9" width="12" height="12" rx="2"></rect><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"></path>';
                }
            }, 1500);
        });
    }

    const playersList = document.getElementById("players-list");
    const lobbyView = document.getElementById("lobby-view");
    const gameView = document.getElementById("game-view");
    const finishedView = document.getElementById("finished-view");
    const gameProgress = document.getElementById("game-progress");
    const gameAudio = document.getElementById("game-audio");
    const audioUnlock = document.getElementById("audio-unlock");
    const buzzButton = document.getElementById("buzz-button");
    const buzzStatus = document.getElementById("buzz-status");
    const answerForm = document.getElementById("answer-form");
    const answerInput = document.getElementById("answer-input");
    const answerSubmit = document.getElementById("answer-submit");
    const roundResult = document.getElementById("round-result");
    const nextRoundButton = document.getElementById("next-round-button");

    function showView(view) {
        [lobbyView, gameView, finishedView].forEach((el) => {
            if (el) {
                el.hidden = el !== view;
            }
        });
    }

    function setText(el, text) {
        if (el) {
            el.textContent = text;
        }
    }

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

    function tryAutoplay() {
        if (!gameAudio) {
            return;
        }
        const playPromise = gameAudio.play();
        if (playPromise && typeof playPromise.catch === "function") {
            playPromise.catch(() => {
                if (audioUnlock) {
                    audioUnlock.hidden = false;
                }
            });
        }
    }

    function startRound(data) {
        showView(gameView);
        setText(gameProgress, `Morceau ${data.played}/${data.total}`);

        if (gameAudio) {
            gameAudio.src = data.preview_url;
        }
        if (audioUnlock) {
            audioUnlock.hidden = true;
        }
        tryAutoplay();

        if (buzzButton) {
            buzzButton.disabled = false;
        }
        if (buzzStatus) {
            buzzStatus.hidden = true;
            setText(buzzStatus, "");
        }
        if (answerForm) {
            answerForm.hidden = true;
        }
        if (answerInput) {
            answerInput.value = "";
            answerInput.disabled = false;
        }
        if (answerSubmit) {
            answerSubmit.disabled = false;
        }
        if (roundResult) {
            roundResult.hidden = true;
            roundResult.textContent = "";
        }
        if (nextRoundButton) {
            nextRoundButton.hidden = true;
            nextRoundButton.disabled = false;
        }
    }

    function lockBuzz(lockedBy) {
        if (buzzButton) {
            buzzButton.disabled = true;
        }
        if (buzzStatus) {
            buzzStatus.hidden = false;
            setText(buzzStatus, lockedBy === player ? "À toi de répondre !" : `${lockedBy} a buzzé...`);
        }
        if (answerForm) {
            answerForm.hidden = lockedBy !== player;
            if (lockedBy === player && answerInput) {
                answerInput.focus();
            }
        }
    }

    function showResult(data) {
        if (buzzStatus) {
            buzzStatus.hidden = true;
        }
        if (answerForm) {
            answerForm.hidden = true;
        }
        if (roundResult) {
            roundResult.hidden = false;
            roundResult.textContent = "";

            const verdict = document.createElement("span");
            verdict.textContent = data.correct ? "Bonne réponse !" : "Raté...";
            roundResult.appendChild(verdict);

            const details = document.createElement("span");
            details.className = "note--em";
            details.textContent = ` — ${data.title} · ${data.artist}`;
            roundResult.appendChild(details);
        }
        if (isHost && nextRoundButton) {
            nextRoundButton.hidden = false;
        }
    }

    function gameOver() {
        showView(finishedView);
    }

    if (buzzButton) {
        buzzButton.addEventListener("click", async () => {
            buzzButton.disabled = true;
            try {
                const response = await fetch(`/rooms/${roomCode}/buzz`, {
                    method: "POST",
                    headers: { "Content-Type": "application/x-www-form-urlencoded" },
                    body: new URLSearchParams({ player }),
                });
                const data = await response.json();
                if (!data.locked) {
                    // Quelqu'un d'autre a buzzé avant, ou trop tard : le message
                    // "buzz_locked" du WebSocket met de toute façon l'UI à jour.
                }
            } catch (err) {
                buzzButton.disabled = false;
            }
        });
    }

    if (audioUnlock) {
        audioUnlock.addEventListener("click", () => {
            tryAutoplay();
            audioUnlock.hidden = true;
        });
    }

    if (answerForm) {
        answerForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const answerText = answerInput.value.trim();
            if (!answerText) {
                return;
            }
            // Désactive pendant l'appel (validation Groq potentielle) pour éviter
            // un double-clic qui déclencherait deux appels/coûts pour la même réponse.
            answerSubmit.disabled = true;
            answerInput.disabled = true;
            try {
                await fetch(`/rooms/${roomCode}/answer`, {
                    method: "POST",
                    headers: { "Content-Type": "application/x-www-form-urlencoded" },
                    body: new URLSearchParams({ player, answer_text: answerText }),
                });
                // Le résultat officiel arrive à tout le monde via "round_result".
            } catch (err) {
                answerSubmit.disabled = false;
                answerInput.disabled = false;
            }
        });
    }

    if (nextRoundButton) {
        nextRoundButton.addEventListener("click", () => {
            nextRoundButton.disabled = true;
            const form = document.createElement("form");
            form.method = "post";
            form.action = `/rooms/${roomCode}/start`;
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "player";
            input.value = player;
            form.appendChild(input);
            document.body.appendChild(form);
            form.submit();
        });
    }

    function connect() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${protocol}://${window.location.host}/ws/${roomCode}`);

        ws.addEventListener("message", (event) => {
            const message = JSON.parse(event.data);
            switch (message.type) {
                case "players_update":
                    renderPlayers(message.players);
                    break;
                case "playlist_loaded": {
                    // Navigue vers une URL propre plutôt qu'un reload() brut : celui-ci
                    // réutiliserait l'URL courante, qui peut encore porter un
                    // ?error=... périmé d'un essai précédent.
                    const url = new URL(window.location.href);
                    url.searchParams.delete("error");
                    url.searchParams.set("loaded", message.track_count);
                    window.location.href = url.pathname + url.search;
                    break;
                }
                case "round_start":
                    startRound(message);
                    break;
                case "buzz_locked":
                    lockBuzz(message.locked_by);
                    break;
                case "round_result":
                    showResult(message);
                    break;
                case "game_over":
                    gameOver();
                    break;
                default:
                    break;
            }
        });

        ws.addEventListener("close", () => {
            setTimeout(connect, 1500);
        });
    }

    // Au chargement (ou après un rafraîchissement en pleine manche), tente de
    // lancer l'extrait déjà rendu côté serveur.
    if (main.dataset.roomStatus === "playing" && gameAudio && gameAudio.getAttribute("src")) {
        tryAutoplay();
    }

    connect();
})();
