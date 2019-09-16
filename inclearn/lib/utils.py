import datetime
import os
import warnings

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import torch
from sklearn import manifold
from sklearn.cluster import KMeans
from sklearn.neighbors import KNeighborsClassifier


def to_onehot(targets, n_classes):
    onehot = torch.zeros(targets.shape[0], n_classes).to(targets.device)
    onehot.scatter_(dim=1, index=targets.long().view(-1, 1), value=1.)
    return onehot


def _check_loss(loss):
    return not bool(torch.isnan(loss).item()) and bool((loss >= 0.).item())


def compute_accuracy(ypred, ytrue, task_size=10):
    all_acc = {}

    all_acc["total"] = round((ypred == ytrue).sum() / len(ytrue), 3)

    for class_id in range(0, np.max(ytrue), task_size):
        idxes = np.where(np.logical_and(ytrue >= class_id, ytrue < class_id + task_size))[0]

        label = "{}-{}".format(
            str(class_id).rjust(2, "0"),
            str(class_id + task_size - 1).rjust(2, "0")
        )
        all_acc[label] = round((ypred[idxes] == ytrue[idxes]).sum() / len(idxes), 3)

    return all_acc


def get_date():
    return datetime.datetime.now().strftime("%Y%m%d")


def extract_features(model, loader):
    targets, features = [], []

    state = model.training
    model.eval()

    for _inputs, _targets, _ in loader:
        _targets = _targets.numpy()
        _features = model.extract(_inputs.to(model.device)).detach().cpu().numpy()

        features.append(_features)
        targets.append(_targets)

    model.train(state)

    return np.concatenate(features), np.concatenate(targets)


def classify(model, loader):
    targets, predictions = [], []

    for _inputs, _targets, _ in loader:
        _targets = _targets.numpy()
        outputs = model(_inputs.to(model.device))
        if not isinstance(outputs, list):
            outputs = [outputs]

        preds = outputs[-1].argmax(dim=1).detach().cpu().numpy()

        predictions.append(preds)
        targets.append(_targets)

    return np.concatenate(predictions), np.concatenate(targets)


def plot_tsne(path, embeddings, targets):
    assert embeddings.shape[0] == targets.shape[0]

    tsne = manifold.TSNE(n_components=2)

    embeddings_2d = tsne.fit_transform(embeddings)
    plt.scatter(
        embeddings_2d[..., 0],
        embeddings_2d[..., 1],
        c=targets,
        vmin=min(targets),
        vmax=max(targets),
        s=10,
        cmap=mpl.cm.get_cmap('RdYlBu')
    )

    directory = os.path.dirname(path)
    if not os.path.exists(directory):
        os.makedirs(directory)
    plt.savefig(path)


def add_new_weights(network, weight_generation, current_nb_classes, task_size, inc_dataset):
    if isinstance(weight_generation, str):
        warnings.warn("Use a dict for weight_generation instead of str", DeprecationWarning)
        weight_generation = {"type": weight_generation}

    if weight_generation["type"] == "imprinted":
        print("Generating imprinted weights")

        network.add_imprinted_classes(
            list(range(current_nb_classes, current_nb_classes + task_size)), inc_dataset
        )
    elif weight_generation["type"] == "embedding":
        print("Generating embedding weights")

        mean_embeddings = []
        for class_index in range(current_nb_classes, current_nb_classes + task_size):
            _, loader = inc_dataset.get_custom_loader([class_index])
            features, _ = extract_features(network, loader)
            features = features / np.linalg.norm(features, axis=-1)[..., None]

            mean = np.mean(features, axis=0)
            if weight_generation.get("proxy_per_class", 1) == 1:
                mean_embeddings.append(mean)
            else:
                std = np.std(features, axis=0, ddof=1)
                mean_embeddings.extend(
                    [
                        np.random.normal(loc=mean, scale=std)
                        for _ in range(weight_generation.get("proxy_per_class", 1))
                    ]
                )

        network.add_custom_weights(np.stack(mean_embeddings))
    elif weight_generation["type"] == "basic":
        network.add_classes(task_size)
    else:
        raise ValueError("Unknown weight generation type {}.".format(weight_generation["type"]))


def apply_kmeans(features, targets, nb_clusters, pre_normalization):
    print(
        "Kmeans on {} samples (pre-normalized: {}) with {} clusters per class".format(
            len(features), pre_normalization, nb_clusters
        )
    )

    new_features = []
    new_targets = []
    for class_index in np.unique(targets):
        kmeans = KMeans(n_clusters=nb_clusters)

        class_sample_indexes = np.where(targets == class_index)[0]
        class_features = features[class_sample_indexes]
        class_targets = np.ones((nb_clusters,)) * class_index

        if pre_normalization:
            class_features = class_features / np.linalg.norm(class_features, axis=-1).reshape(-1, 1)

        kmeans.fit(class_features)
        new_features.append(kmeans.cluster_centers_)
        new_targets.append(class_targets)

    return np.concatenate(new_features), np.concatenate(new_targets)


def apply_knn(features, targets, features_test, targets_test, nb_neighbors, pre_normalize):
    print(
        "KNN with {} neighbors and pre-normalized features: {}.".format(
            nb_neighbors, pre_normalize
        )
    )

    if pre_normalize:
        features = features / np.linalg.norm(features, axis=-1).reshape(-1, 1)

    knn = KNeighborsClassifier(n_neighbors=nb_neighbors, n_jobs=10)
    knn.fit(features, targets)

    if pre_normalize:
        features_test = features_test / np.linalg.norm(features_test, axis=-1).reshape(-1, 1)

    pred_targets = knn.predict(features_test)

    return pred_targets, targets_test


def select_class_samples(samples, targets, selected_class):
    indexes = np.where(targets == selected_class)[0]
    return samples[indexes], targets[indexes]
