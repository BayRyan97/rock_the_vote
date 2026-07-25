"""gtn.py — the GraphGPS multi-task model (shared GPSConv encoder + two heads).

Architecture (research doc §2):
  input  = encoder numerics + categorical embeddings + RWSE projection
  encoder = n_layers x GPSConv(GINEConv local MPNN, performer global attention),
            edge-type embedding as edge_attr
  turnout head = MLP(h ++ own-party embedding)      -> 1 logit   (BCE)
  party head   = MLP(h ++ tier feats/embedding)     -> 3 logits  (CE, BLK masked)

Leakage: the encoder consumes only manifest 'encoder' features. Party
registration enters ONLY the turnout head; tier features ONLY the party head.
"""
import torch
import torch.nn as nn
from torch_geometric.nn import GINEConv, GPSConv


def emb_dim(cardinality: int) -> int:
    return min(16, max(4, round(cardinality ** 0.5) + 1))


class CatEmbeddings(nn.Module):
    """One embedding per categorical column, concatenated."""

    def __init__(self, cardinalities: list[int]):
        super().__init__()
        self.embs = nn.ModuleList([nn.Embedding(c, emb_dim(c)) for c in cardinalities])
        self.out_dim = sum(emb_dim(c) for c in cardinalities)

    def forward(self, codes: torch.Tensor) -> torch.Tensor:
        if not self.embs:
            return codes.new_zeros(codes.shape[0], 0, dtype=torch.float32)
        return torch.cat([emb(codes[:, i]) for i, emb in enumerate(self.embs)], dim=1)


def mlp(dims: list[int], dropout: float = 0.1) -> nn.Sequential:
    layers = []
    for a, b in zip(dims[:-1], dims[1:]):
        layers += [nn.Linear(a, b), nn.ReLU(), nn.Dropout(dropout)]
    return nn.Sequential(*layers[:-2])  # strip trailing ReLU+Dropout


class VoterGTN(nn.Module):
    def __init__(self, meta: dict, rwse_k: int, n_edge_types: int,
                 hidden: int = 128, n_layers: int = 3, heads: int = 4,
                 attn_type: str = "performer", dropout: float = 0.1):
        super().__init__()
        enc_num_dim = len(meta["encoder_num_stats"]["cols"])
        self.enc_cats = CatEmbeddings(meta["encoder_cat_cards"])
        self.rwse_proj = nn.Linear(rwse_k, 24)
        in_dim = enc_num_dim + self.enc_cats.out_dim + 24
        self.input_proj = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout))

        self.edge_emb = nn.Embedding(n_edge_types, 16)
        self.convs = nn.ModuleList()
        for _ in range(n_layers):
            local = GINEConv(mlp([hidden, hidden, hidden], dropout), edge_dim=16)
            self.convs.append(GPSConv(hidden, local, heads=heads,
                                      attn_type=attn_type, dropout=dropout))

        # turnout head: h ++ own party registration embedding
        self.turnout_cats = CatEmbeddings(meta["turnout_cat_cards"])
        t_num = len(meta.get("turnout_num_stats", {}).get("cols", []))
        self.turnout_head = mlp([hidden + self.turnout_cats.out_dim + t_num, 64, 1], dropout)

        # party head: h ++ tier letter embedding ++ tier numerics
        self.party_cats = CatEmbeddings(meta["party_cat_cards"])
        p_num = len(meta.get("party_num_stats", {}).get("cols", []))
        self.party_head = mlp([hidden + self.party_cats.out_dim + p_num, 64, 3], dropout)

    def forward(self, batch) -> tuple[torch.Tensor, torch.Tensor]:
        x = torch.cat([batch.enc_num, self.enc_cats(batch.enc_cat),
                       self.rwse_proj(batch.rwse)], dim=1)
        h = self.input_proj(x)
        edge_attr = self.edge_emb(batch.edge_type.long())
        for conv in self.convs:
            h = conv(h, batch.edge_index, batch=getattr(batch, "batch", None),
                     edge_attr=edge_attr)
        t_in = torch.cat([h, self.turnout_cats(batch.turnout_cat), batch.turnout_num], dim=1)
        p_in = torch.cat([h, self.party_cats(batch.party_cat), batch.party_num], dim=1)
        return self.turnout_head(t_in).squeeze(-1), self.party_head(p_in)
