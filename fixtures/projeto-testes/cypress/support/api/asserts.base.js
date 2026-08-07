// Invariantes que valem para qualquer recurso: status exato e não vazamento.
// Recebem respostas prontas — nunca fazem request.

const VAZAMENTOS = [/stack/i, /SQLSTATE/i, /at [\w.$]+\(/];

export function statusExato(resposta, esperado, contexto) {
  expect(resposta.status, `${contexto} deve responder ${esperado}`).to.eq(esperado);
}

export function semVazamentoInterno(resposta, contexto) {
  const corpo = JSON.stringify(resposta.body ?? "");
  VAZAMENTOS.forEach((padrao) => {
    expect(padrao.test(corpo), `${contexto} não deve vazar detalhe interno`).to.eq(false);
  });
}
