/* Terra do Agro — armazenamento offline (IndexedDB) e fila de sincronização.

   Isolamento: cada usuário tem seu próprio banco "terra-offline-<chave>", onde <chave> é um identificador
   opaco gerado pelo servidor. Não guardamos senha, token de sessão nem dados financeiros.

   Fila: cada operação recebe um UUID. O servidor é idempotente por UUID, então reenviar a mesma operação
   (por exemplo, após queda de conexão no meio do envio) nunca cria registros duplicados. Uma operação só é
   removida da fila depois que o servidor confirma "aplicada" ou "duplicada". */
(function (global) {
  "use strict";

  var VERSAO_DB = 1;
  var conexoes = {};
  var sincronizando = {};

  function nomeDb(chave) { return "terra-offline-" + chave; }

  function abrir(chave) {
    if (!chave) return Promise.reject(new Error("Usuário não identificado."));
    if (!("indexedDB" in global)) return Promise.reject(new Error("Este navegador não oferece armazenamento offline (IndexedDB)."));
    if (conexoes[chave]) return conexoes[chave];
    conexoes[chave] = new Promise(function (resolve, reject) {
      var req = indexedDB.open(nomeDb(chave), VERSAO_DB);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains("snapshot")) db.createObjectStore("snapshot");
        if (!db.objectStoreNames.contains("fila")) {
          var fila = db.createObjectStore("fila", { keyPath: "uuid" });
          fila.createIndex("criado_em", "criado_em");
        }
        if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta");
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { delete conexoes[chave]; reject(req.error); };
    });
    return conexoes[chave];
  }

  function tx(chave, store, modo, fn) {
    return abrir(chave).then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(store, modo);
        var resultado;
        t.oncomplete = function () { resolve(resultado); };
        t.onerror = function () { reject(t.error); };
        t.onabort = function () { reject(t.error || new Error("Transação cancelada.")); };
        resultado = fn(t.objectStore(store));
        if (resultado && "onsuccess" in resultado) {
          var r = resultado;
          r.onsuccess = function () { resultado = r.result; };
        }
      });
    });
  }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }

  // ---------------------------------------------------------------- Dados sincronizados (somente leitura offline)
  function salvarSnapshot(chave, dados) {
    return tx(chave, "snapshot", "readwrite", function (s) { return s.put(dados, "dados"); }).then(function () {
      return tx(chave, "meta", "readwrite", function (s) { return s.put(Date.now(), "snapshot_em"); });
    });
  }

  function lerSnapshot(chave) {
    return tx(chave, "snapshot", "readonly", function (s) { return s.get("dados"); });
  }

  function snapshotEm(chave) {
    return tx(chave, "meta", "readonly", function (s) { return s.get("snapshot_em"); });
  }

  function baixarSnapshot(chave) {
    return fetch("/api/offline/snapshot", { credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        var tipo = r.headers.get("Content-Type") || "";
        if (!r.ok || tipo.indexOf("application/json") === -1) throw new Error("Sessão expirada. Entre novamente para atualizar os dados.");
        return r.json();
      })
      .then(function (dados) { return salvarSnapshot(chave, dados).then(function () { return dados; }); });
  }

  function atualizarSeAntigo(chave, idadeMs) {
    if (!navigator.onLine) return Promise.resolve(null);
    return snapshotEm(chave).then(function (em) {
      if (em && Date.now() - em < idadeMs) return null;
      return baixarSnapshot(chave);
    });
  }

  // ---------------------------------------------------------------- Fila de operações
  function enfileirar(chave, tipo, propriedadeId, dados) {
    var op = { uuid: uuid(), tipo: tipo, propriedade_id: propriedadeId, dados: dados, criado_em: new Date().toISOString(),
               status: "pendente", mensagem: "", tentativas: 0, forcar: false };
    return tx(chave, "fila", "readwrite", function (s) { return s.add(op); }).then(function () { return op; });
  }

  function listarFila(chave) {
    return tx(chave, "fila", "readonly", function (s) { return s.getAll(); }).then(function (l) {
      return (l || []).sort(function (a, b) { return a.criado_em < b.criado_em ? -1 : 1; });
    });
  }

  function atualizarOp(chave, op) { return tx(chave, "fila", "readwrite", function (s) { return s.put(op); }); }
  function removerOp(chave, id) { return tx(chave, "fila", "readwrite", function (s) { return s.delete(id); }); }

  function contarPendentes(chave) {
    return listarFila(chave).then(function (l) { return l.length; }).catch(function () { return 0; });
  }

  function obterCsrf(chave) {
    return fetch("/api/csrf", { credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        var tipo = r.headers.get("Content-Type") || "";
        if (!r.ok || tipo.indexOf("application/json") === -1) {
          var e = new Error("Entre na sua conta para sincronizar.");
          e.precisaLogin = true;
          throw e;
        }
        return r.json();
      })
      .then(function (d) {
        if (d.chave !== chave) {
          var e = new Error("A sessão aberta é de outro usuário. Entre com a conta que fez os registros offline.");
          e.precisaLogin = true;
          throw e;
        }
        return d.token;
      });
  }

  function enviarOp(chave, op, token) {
    op.status = "enviando";
    op.tentativas = (op.tentativas || 0) + 1;
    return atualizarOp(chave, op).then(function () {
      return fetch("/api/sync", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": token },
        body: JSON.stringify({ uuid: op.uuid, tipo: op.tipo, propriedade_id: op.propriedade_id, dados: op.dados, forcar: !!op.forcar })
      });
    }).then(function (r) {
      return r.json().catch(function () { return { status: "erro", mensagem: "Resposta inválida do servidor (" + r.status + ")." }; })
        .then(function (d) { d.http = r.status; return d; });
    }).then(function (d) {
      if (d.status === "aplicada" || d.status === "duplicada") {
        // Persistência confirmada pelo servidor: só agora removemos a cópia local.
        return removerOp(chave, op.uuid).then(function () { return "ok"; });
      }
      if (d.status === "conflito" || d.status === "rejeitada") {
        op.status = d.status;
        op.mensagem = d.mensagem || "";
        op.forcar = false;
        return atualizarOp(chave, op).then(function () { return d.status; });
      }
      throw new Error(d.mensagem || d.erro || "Falha ao sincronizar.");
    }).catch(function (err) {
      op.status = "erro";
      op.mensagem = (err && err.message) || "Falha de conexão durante o envio.";
      return atualizarOp(chave, op).then(function () { return "erro"; });
    });
  }

  function sincronizarTudo(chave) {
    if (!chave) return Promise.resolve({ enviados: 0 });
    if (!navigator.onLine) return Promise.resolve({ enviados: 0, offline: true });
    if (sincronizando[chave]) return sincronizando[chave];
    var executar = function () {
      return listarFila(chave).then(function (fila) {
        var aEnviar = fila.filter(function (op) { return op.status === "pendente" || op.status === "erro" || op.status === "enviando"; });
        var resumo = { enviados: 0, conflitos: 0, rejeitados: 0, erros: 0, total: aEnviar.length };
        if (!aEnviar.length) return resumo;
        return obterCsrf(chave).then(function (token) {
          // Envio sequencial preserva a ordem dos registros e facilita o tratamento de conflitos.
          return aEnviar.reduce(function (p, op) {
            return p.then(function () {
              return enviarOp(chave, op, token).then(function (res) {
                if (res === "ok") resumo.enviados++;
                else if (res === "conflito") resumo.conflitos++;
                else if (res === "rejeitada") resumo.rejeitados++;
                else resumo.erros++;
              });
            });
          }, Promise.resolve()).then(function () { return resumo; });
        });
      });
    };
    var p = (navigator.locks && navigator.locks.request)
      ? navigator.locks.request("terra-sync-" + chave, executar)
      : executar();
    sincronizando[chave] = p.finally(function () { delete sincronizando[chave]; });
    return sincronizando[chave];
  }

  function resolverConflito(chave, id, acao) {
    return tx(chave, "fila", "readonly", function (s) { return s.get(id); }).then(function (op) {
      if (!op) return null;
      if (acao === "descartar") return removerOp(chave, id);
      op.status = "pendente";
      op.forcar = acao === "manter";
      op.mensagem = "";
      return atualizarOp(chave, op);
    });
  }

  // Ao sair da conta: apaga os dados sincronizados do aparelho, mantendo apenas a fila não enviada
  // (que só pode ser enviada pela mesma conta, verificado pela chave na sincronização).
  function limparDadosPrivados(chave) {
    if (!chave) return Promise.resolve();
    return Promise.all([
      tx(chave, "snapshot", "readwrite", function (s) { return s.clear(); }),
      tx(chave, "meta", "readwrite", function (s) { return s.clear(); })
    ]).then(function () {
      return listarFila(chave).then(function (l) {
        if (l.length) return null;
        return abrir(chave).then(function (db) {
          db.close();
          delete conexoes[chave];
          return new Promise(function (resolve) {
            var r = indexedDB.deleteDatabase(nomeDb(chave));
            r.onsuccess = r.onerror = r.onblocked = function () { resolve(); };
          });
        });
      });
    }).catch(function () {});
  }

  global.TAOffline = {
    uuid: uuid,
    saveSnapshot: salvarSnapshot,
    getSnapshot: lerSnapshot,
    snapshotTime: snapshotEm,
    downloadSnapshot: baixarSnapshot,
    refreshSnapshotIfOld: atualizarSeAntigo,
    enqueue: enfileirar,
    listQueue: listarFila,
    countPending: contarPendentes,
    syncAll: sincronizarTudo,
    resolve: resolverConflito,
    remove: removerOp,
    clearPrivateData: limparDadosPrivados
  };
})(window);
