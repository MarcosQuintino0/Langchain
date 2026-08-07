// Fonte única dos caminhos relativos confirmados no backend.
export const RotasApi = Object.freeze({
  pedidos: Object.freeze({
    colecao: "/pedidos",
    porId: (id) => `/pedidos/${id}`,
  }),
});
