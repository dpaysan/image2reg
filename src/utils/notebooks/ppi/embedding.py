import numpy as np
import pandas as pd
import seaborn as sns
import torch
import torch_geometric.transforms as T
from matplotlib import pyplot as plt
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE, MDS
from sklearn.metrics import adjusted_mutual_info_score
from torch_geometric.nn import Node2Vec
from torch_geometric.utils import remove_self_loops
from tqdm import tqdm

from src.utils.torch.general import get_device
from src.utils.torch.network import (
    train_n2v_model,
    train_gae,
    add_pos_negative_edge_indices,
)


def get_gae_latents_for_seed(
    graph_data,
    gae,
    seeds,
    node_feature_key,
    edge_weight_key=None,
    feature_decoder=None,
    latent_classifier=None,
    data_dict=None,
    split_type=None,
    alpha=1,
    beta=1,
    gamma=1,
    lr=1e-3,
    wd=0,
    n_epochs=100,
    early_stopping=50,
    plot_loss=False,
    use_full_graph=False,
    neg_edge_ratio=1.0,
):
    latents_dict = {}
    loss_hist_dict = {}
    for seed in seeds:
        gae.reset_parameters()
        if feature_decoder is not None:
            feature_decoder.reset_parameters()
        if latent_classifier is not None:
            latent_classifier.reset_parameters()

        # Ensure reproducibility
        torch.manual_seed(seed)
        torch.backends.cudnn.deterministic = True

        # No train-val-test split
        if data_dict is None:
            if split_type is None:
                modified_graph_data = graph_data
                modified_graph_data.pos_edge_label_index = (
                    modified_graph_data.edge_index
                )

                modified_graph_data = add_pos_negative_edge_indices(modified_graph_data)
                # remove self-loops:
                modified_graph_data.neg_edge_index, _ = remove_self_loops(
                    modified_graph_data.neg_edge_index
                )

                data_dict = {
                    "train": modified_graph_data,
                    "val": modified_graph_data,
                    "test": modified_graph_data,
                }
            elif split_type == "link":
                random_link_splitter = T.RandomLinkSplit(
                    is_undirected=True,
                    add_negative_train_samples=True,
                    num_val=0.1,
                    num_test=0.2,
                    split_labels=True,
                    neg_sampling_ratio=neg_edge_ratio,
                )
                train_link_data, val_link_data, test_link_data = random_link_splitter(
                    graph_data
                )
                if use_full_graph:
                    train_link_data.edge_index = graph_data.edge_index
                    val_link_data.edge_index = graph_data.edge_index
                    test_link_data.edge_index = graph_data.edge_index

                    if edge_weight_key is not None:
                        setattr(
                            train_link_data,
                            edge_weight_key,
                            getattr(graph_data, edge_weight_key),
                        )
                        setattr(
                            val_link_data,
                            edge_weight_key,
                            getattr(graph_data, edge_weight_key),
                        )
                        setattr(
                            test_link_data,
                            edge_weight_key,
                            getattr(graph_data, edge_weight_key),
                        )

                data_dict = {
                    "train": train_link_data,
                    "val": val_link_data,
                    "test": test_link_data,
                }
            elif split_type == "node":
                raise NotImplementedError()
            else:
                raise NotImplementedError()

        model_parameters = list(gae.parameters())
        if feature_decoder is not None:
            model_parameters += list(feature_decoder.parameters())
        if latent_classifier is not None:
            model_parameters += list(latent_classifier.parameters())

        optimizer = torch.optim.Adam(model_parameters, lr=lr, weight_decay=wd)
        gae, feature_decoder, latent_classifier, loss_hist = train_gae(
            gae=gae,
            data_dict=data_dict,
            node_feature_key=node_feature_key,
            edge_weight_key=edge_weight_key,
            optimizer=optimizer,
            n_epochs=n_epochs,
            early_stopping=early_stopping,
            link_pred=split_type == "link",
            feature_decoder=feature_decoder,
            latent_classifier=latent_classifier,
            alpha=alpha,
            beta=beta,
            gamma=gamma,
        )

        gae.eval()
        graph_data = graph_data.to(gae.device)
        inputs = getattr(graph_data, node_feature_key).float()
        latents = gae.encode(inputs, graph_data.edge_index)
        latents = latents.detach().cpu().numpy()

        latents_dict[seed] = latents
        loss_hist_dict[seed] = loss_hist

        if plot_loss:
            train_loss_hist = loss_hist.loc[
                (loss_hist.loc[:, "mode"] == "train") & (loss_hist.loc[:, "epoch"] > -1)
            ]
            val_loss_hist = loss_hist.loc[
                (loss_hist.loc[:, "mode"] == "val") & (loss_hist.loc[:, "epoch"] > -1)
            ]
            epochs = np.array(train_loss_hist.loc[:, "epoch"])
            fig, ax = plt.subplots(figsize=[12, 4], ncols=2)
            ax = ax.flatten()
            ax[0] = sns.lineplot(
                data=loss_hist.loc[loss_hist.loc[:, "epoch"] > -1],
                x="epoch",
                y="total_loss",
                hue="mode",
                ax=ax[0],
            )

            ax[1].plot(
                epochs,
                np.array(train_loss_hist.loc[:, "gae_recon_loss",]),
                c="tab:blue",
                label="train_gae_recon",
                linestyle="-.",
            )
            ax[1].plot(
                epochs,
                np.array(train_loss_hist.loc[:, "feat_recon_loss",],),
                c="tab:blue",
                label="train_feat_recon",
                linestyle="--",
            )
            ax[1].plot(
                epochs,
                np.array(train_loss_hist.loc[:, "class_loss",],),
                c="tab:blue",
                label="cluster",
                linestyle=":",
            )

            ax[1].plot(
                epochs,
                np.array(val_loss_hist.loc[:, "gae_recon_loss",]),
                c="tab:orange",
                label="val_gae_recon",
                linestyle="-.",
            )
            ax[1].plot(
                epochs,
                np.array(val_loss_hist.loc[:, "feat_recon_loss",],),
                c="tab:orange",
                linestyle="--",
                label="val_feat_recon",
            )
            ax[1].plot(
                epochs,
                np.array(val_loss_hist.loc[:, "class_loss",],),
                c="tab:orange",
                label="val_cluster",
                linestyle=":",
            )

            ax[1].set_xlabel("epoch")
            ax[1].set_ylabel("loss")
            plt.legend()
            plt.show()

    return latents_dict, loss_hist_dict


def get_n2v_latents_for_seed(
    graph_data,
    seeds,
    latent_dim=64,
    walk_length=30,
    context_size=10,
    walks_per_node=50,
    batch_size=128,
    num_workers=10,
    lr=0.01,
    n_epochs=100,
    plot_loss=False,
    device=None,
):
    if device is None:
        device = get_device()
        # device = torch.device("cpu")

    latents_dict = {}
    for seed in seeds:
        # Change initialization
        torch.manual_seed(seed)
        torch.backends.cudnn.deterministic = True

        n2v_model = Node2Vec(
            graph_data.edge_index,
            embedding_dim=latent_dim,
            walk_length=walk_length,
            context_size=context_size,
            walks_per_node=walks_per_node,
            num_negative_samples=1,
            p=1,
            q=1,
            sparse=True,
        ).to(device)
        n2v_model.device = device

        n2v_loader = n2v_model.loader(
            batch_size=batch_size, shuffle=True, num_workers=num_workers
        )
        n2v_optimizer = torch.optim.SparseAdam(list(n2v_model.parameters()), lr=lr)

        fitted_n2v_model, loss_hist = train_n2v_model(
            model=n2v_model,
            optimizer=n2v_optimizer,
            loader=n2v_loader,
            n_epochs=n_epochs,
        )

        fitted_n2v_model.eval()
        latents = (
            fitted_n2v_model(torch.arange(graph_data.num_nodes, device=device))
            .cpu()
            .detach()
            .numpy()
        )

        latents_dict[seed] = latents

        if plot_loss:
            fig, ax = plt.subplots(figsize=[6, 4])
            ax.plot(np.arange(1, n_epochs + 1), loss_hist)
            fig.suptitle("Loss during training")
            ax.set_xlabel("Epoch")
            ax.set_ylabel("Loss")
            plt.show()
    return latents_dict


def stability_cocluster_screen(latents_dict, affinity="euclidean", linkage="average"):
    ami_matrices = []
    ks = latents_dict.keys()
    latents = list(latents_dict.values())
    for i in range(len(latents)):
        for j in tqdm(range(len(latents))):
            ami_matrices.append(
                compute_ami_matrix(
                    latents[i], latents[j], affinity=affinity, linkage=linkage
                )
            )
    return ami_matrices


def compute_ami_matrix(
    latents_1, latents_2, affinity="euclidean", linkage="average", n_max_clusters=15
):
    ami = np.zeros([n_max_clusters, n_max_clusters])
    if isinstance(affinity, str):
        affinity_1 = affinity_2 = affinity
    else:
        affinity_1, affinity_2 = affinity[0], affinity[1]
    if isinstance(linkage, str):
        linkage_1 = linkage_2 = linkage
    else:
        linkage_1, linkage_2 = linkage[0], linkage[1]

    for i in range(0, n_max_clusters):
        cluster_sol1 = AgglomerativeClustering(
            affinity=affinity_1, n_clusters=i + 1, linkage=linkage_1
        ).fit_predict(latents_1)
        #             cluster_sol1 = KMeans(random_state=0, n_clusters=i+1).fit_predict(latents_2)
        for j in range(0, n_max_clusters):
            #                 cluster_sol2 = KMeans(random_state=0, n_clusters=j+1).fit_predict(latents_2)
            cluster_sol2 = AgglomerativeClustering(
                affinity=affinity_2, n_clusters=j + 1, linkage=linkage_2
            ).fit_predict(latents_2)

            ami[i, j] = adjusted_mutual_info_score(cluster_sol1, cluster_sol2)
    return ami


def plot_amis_matrices(names, amis, figsize=[30, 30]):
    fig, ax = plt.subplots(figsize=figsize, ncols=len(names), nrows=len(names))
    ax = ax.flatten()
    for i in range(len(names)):
        for j in range(len(names)):
            if amis[i] is not None:
                ax[j + i * len(names)] = sns.heatmap(
                    amis[i * len(names) + j],
                    ax=ax[i * len(names) + j],
                    vmin=0,
                    vmax=1,
                    cbar=(j == len(names) - 1),
                    cmap="seismic",
                )
                ax[j + i * len(names)].set_title(
                    "max AMI: {:.2f}".format(np.max(amis[i * len(names) + j][1:, 1:])),
                )
            if i == j:
                ax[i * len(names) + j].set_title(
                    "Model: {}".format(names[j]), weight="bold", c="red"
                )
            ax[j + i * len(names)].set_xticks(
                [k for k in range(len(amis[i * len(names) + j]))]
            )
            ax[j + i * len(names)].set_xticklabels(
                [k + 1 for k in range(len(amis[i * len(names) + j]))]
            )
            ax[j + i * len(names)].set_yticks(
                [k for k in range(len(amis[i * len(names) + j]))]
            )
            ax[j + i * len(names)].set_yticklabels(
                [k + 1 for k in range(len(amis[i * len(names) + j]))]
            )
    plt.show()


def plot_tsne_embs(
    latents,
    ax,
    random_state=1234,
    perplexity=16,
    size=10,
    hue=None,
    hue_order=None,
    palette=None,
    label_points=None,
):
    embs = TSNE(
        random_state=random_state,
        perplexity=perplexity,
        init="pca",
        learning_rate="auto",
        n_jobs=5,
    ).fit_transform(latents)
    embs = pd.DataFrame(embs, columns=["tsne_0", "tsne_1"], index=latents.index)
    ax = sns.scatterplot(
        data=embs,
        x="tsne_0",
        y="tsne_1",
        cmap="viridis",
        ax=ax,
        hue=hue,
        hue_order=hue_order,
        palette=palette,
    )
    if label_points is not None:
        to_label_embs = embs.loc[embs.index.isin(label_points)]
        label_point(
            np.array(to_label_embs.loc[:, "tsne_0"]),
            np.array(to_label_embs.loc[:, "tsne_1"]),
            np.array(to_label_embs.index).astype("str"),
            ax=ax,
            size=size,
        )
    return ax


def plot_mds_embs(
    latents,
    ax,
    dissimilarity="precomputed",
    size=10,
    hue=None,
    hue_order=None,
    palette=None,
    label_points=None,
    random_state=1234,
):
    embs = MDS(
        n_components=2, dissimilarity=dissimilarity, random_state=random_state
    ).fit_transform(latents)
    embs = pd.DataFrame(embs, columns=["mds_0", "mds_1"], index=latents.index)
    ax = sns.scatterplot(
        data=embs,
        x="mds_0",
        y="mds_1",
        cmap="viridis",
        ax=ax,
        hue=hue,
        hue_order=hue_order,
        palette=palette,
    )
    if label_points is not None:
        to_label_embs = embs.loc[embs.index.isin(label_points)]
        label_point(
            np.array(to_label_embs.loc[:, "mds_0"]),
            np.array(to_label_embs.loc[:, "mds_1"]),
            np.array(to_label_embs.index).astype("str"),
            ax=ax,
            size=size,
        )
    return ax


def plot_pca_embs(
    latents,
    ax,
    size=10,
    hue=None,
    hue_order=None,
    palette=None,
    label_points=None,
    highlight=None,
    random_state=1234,
):
    embs = PCA(n_components=2, random_state=random_state).fit_transform(latents)
    embs = pd.DataFrame(embs, columns=["pc_0", "pc_1"], index=latents.index)
    ax = sns.scatterplot(
        data=embs,
        x="pc_0",
        y="pc_1",
        cmap="viridis",
        ax=ax,
        hue=hue,
        hue_order=hue_order,
        palette=palette,
    )
    if label_points is not None:
        to_label_embs = embs.loc[embs.index.isin(label_points)]
        label_point(
            np.array(to_label_embs.loc[:, "pc_0"]),
            np.array(to_label_embs.loc[:, "pc_1"]),
            np.array(to_label_embs.index).astype("str"),
            ax=ax,
            size=size,
        )
    return ax


def label_point(x, y, val, ax, size=10):
    for i in range(len(x)):
        ax.text(x[i] + 0.02, y[i], val[i], {"size": size})


def get_rank_difference_dict(latents, tol=1e-5):
    idc = list(latents.index)
    full_rank = np.linalg.matrix_rank(latents, tol=tol)
    delta_rank_dict = {}
    for idx in idc:
        rank = np.linalg.matrix_rank(latents.loc[latents.index != idx], tol=tol)
        if rank != full_rank:
            delta_rank_dict[idx] = full_rank - rank
    return delta_rank_dict
