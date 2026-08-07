package fixture.entities;

@Entity
public class Pedido {

  @Id
  private Long id;

  @NotNull
  @Enumerated(EnumType.STRING)
  private Situacao situacao;

  public enum Situacao {
    ABERTO,
    FECHADO
  }
}
