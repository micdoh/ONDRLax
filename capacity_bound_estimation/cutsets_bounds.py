import networkx as nx
import numpy as np
import itertools
import math
import os
import time
from functools import partial
from absl import flags, app
import jax.numpy as jnp
import jax
import sys
from numpy.lib.utils import source

from xlron.environments.env_funcs import make_graph
from xlron.environments.rsa import make_rsa_env
import xlron.train.parameter_flags
from xlron.train.train_utils import define_env
from xlron.environments.wrappers import TimeIt, HashableArrayWrapper
from xlron.environments.env_funcs import get_paths, get_paths_se, normalise_traffic_matrix

FLAGS = flags.FLAGS


# def find_cut_sets(graph):
#     """Find all cut-sets of a given graph."""
#     cut_sets = []
#     nodes = list(graph.nodes())
#
#     # Generate all possible non-trivial partitions of the node set
#     for i in range(
#             1,  # len(nodes) - min(5, len(nodes)//2),
#             len(nodes)
#     ):
#         for partition in itertools.combinations(nodes, i):
#             S = set(partition)
#             V_minus_S = set(nodes) - S
#
#             cut_set = set()
#             for u in S:
#                 for v in V_minus_S:
#                     if graph.has_edge(u, v):
#                         cut_set.add((u, v))
#
#             if cut_set:
#                 cut_sets.append((S, V_minus_S, cut_set))
#
#     return cut_sets
#
#
# def calculate_congestion_levels(graph, traffic_matrix):
#     """Calculate the congestion levels of all cut-sets."""
#     cut_sets = find_cut_sets(graph)
#     congestion_levels = []
#
#     for S, V_minus_S, cut_set in cut_sets:
#         w_n = 0
#         for s in S:
#             for d in V_minus_S:
#                 w_n += traffic_matrix[s][d]
#         w_n /= len(cut_set)
#         congestion_levels.append((w_n, S, V_minus_S, cut_set))
#
#     return congestion_levels
#
#
# def find_heavy_cut_sets(graph, traffic_matrix, top_n=1):
#     """Find the top_n heavy cut-sets based on congestion levels."""
#     congestion_levels = calculate_congestion_levels(graph, traffic_matrix)
#     # Sort cut-sets by congestion level in descending order
#     congestion_levels.sort(key=lambda x: x[0], reverse=True)
#     # Return the top_n heavy cut-sets
#     return congestion_levels[:top_n]
#
#
# def find_less_congested_edges(self, traffic_matrix, bottom_n=1):
#     """Find the bottom_n less congested edges based on congestion levels."""
#     congestion_levels = self.calculate_congestion_levels(traffic_matrix)
#     # Sort cut-sets by congestion level in ascending order
#     congestion_levels.sort(key=lambda x: x[0])
#     # Return the bottom_n less congested cut-sets
#     return congestion_levels[:bottom_n]
#
#
#
#
# def main_numpy(argv):
#     graph = make_graph(FLAGS.topology_name, FLAGS.topology_directory)
#     _, params = define_env(FLAGS)
#     traffic_matrix = get_weighted_traffic_matrix(graph, params, se_measure='shortest')
#     traffic_matrix = normalise_traffic_matrix(traffic_matrix)
#     print(f"Traffic Matrix:\n{traffic_matrix}\n")
#
#     # Find the heavy cut-sets
#     with TimeIt("Heavy Cut-Set Calculation:"):
#         heavy_cut_sets = find_heavy_cut_sets(graph, traffic_matrix, top_n=10)
#
#     print("Heavy cut-sets with their congestion levels:")
#     for congestion_level, S, V_minus_S, cut_set in heavy_cut_sets:
#         print(
#             f"Congestion Level: {congestion_level}, Cut-set: {cut_set}, Min. Partition Size: {min(len(S), len(V_minus_S))}")
#
#
# def generate_balanced_partition_masks(n_nodes, min_partition_size=1, max_batch_size=10000):
#     """
#     Generate balanced partition masks using Gray code to minimize bit flips.
#     Only generates partitions where smaller set has size >= min_partition_size.
#     """
#     import numpy as np
#
#     def gray_code(n):
#         return n ^ (n >> 1)
#
#     total_combinations = 2**n_nodes
#     masks = jnp.zeros((max_batch_size, n_nodes), dtype=jnp.int32)
#
#     for i in range(total_combinations):
#         gray = gray_code(i)
#         mask = jnp.array([int(b) for b in format(gray, f'0{n_nodes}b')])
#         ones = jnp.sum(mask)
#
#         # Only keep masks with balanced partitions
#         def update_mask(_i, _masks):
#             batch_masks = generate_gray_code_masks(n_nodes, max_batch_size)
#             return jax.lax.dynamic_update_slice(_masks, mask.reshape((n_nodes, 1)), (_i, 0))
#         masks = jax.lax.scan(update_mask, masks, i)
#
#         if len(masks) == max_batch_size:  # Limit batch size for memory
#             yield masks
#
#     if masks:
#         yield masks
#
# def find_minimum_cut(graph, weight=""):
#     """Find the minimum cut-set of a given graph."""
#     cut_value, partition = nx.stoer_wagner(graph, weight=weight)
#     cut_set = set()
#
#     # Extract the nodes in the two partitions
#     S, T = partition
#     for u in S:
#         for v in T:
#             if graph.has_edge(u, v):
#                 cut_set.add((u, v))
#
#     return cut_set, (S, T)


def get_weighted_traffic_matrix(graph, params):
    n_nodes = len(graph.nodes())
    traffic_matrix = jnp.zeros((n_nodes, n_nodes))
    for s in range(n_nodes):
        for d in range(n_nodes):
            if s != d:
                nodes = jnp.array([s, d])
                paths = get_paths(params, nodes)
                se = get_paths_se(params, nodes)
                traffic_matrix = jax.lax.dynamic_update_slice(
                    traffic_matrix,
                    jnp.array(1 / se[0]).reshape((1, 1)),
                    (s, d)
                )
            else:
                traffic_matrix = jax.lax.dynamic_update_slice(traffic_matrix, jnp.array(0.).reshape((1, 1)), (s, d))
    return traffic_matrix
    
    
@partial(jax.jit, static_argnums=(1,))
def make_complete_subgraph(path_adj_matrix, adj_matrix):
    # Create mask of nodes that are part of subgraph
    active = (path_adj_matrix.sum(axis=0) + path_adj_matrix.sum(axis=1)) > 0
    # Create complete connections between active nodes
    result = jnp.outer(active, active) * adj_matrix.val
    return result


@partial(jax.jit, static_argnums=(1, 2, 3))
def edges_to_adjacency(path_array, source_nodes, dest_nodes, num_nodes):
    """
    Convert a binary path array and source/dest node arrays to an adjacency matrix.

    Args:
        path_array: binary array indicating which edges are in path 
        source_nodes: array of source node indices
        dest_nodes: array of destination node indices

    Returns:
        adjacency_matrix: NxN binary adjacency matrix
    """
    adjacency = jnp.zeros((num_nodes, num_nodes))

    # Build adjacency matrix by scattering 1s at source,dest pairs
    def update_adj(idx, adj):
        s, d = source_nodes[idx], dest_nodes[idx]
        update_val = jnp.array([[1.]]) * path_array[idx]
        adj = jax.lax.dynamic_update_slice(adj, update_val, (s,d))
        adj = jax.lax.dynamic_update_slice(adj, update_val, (d,s))
        return adj

    adjacency = jax.lax.fori_loop(0, len(path_array), update_adj, adjacency)

    return adjacency


@partial(jax.jit, static_argnums=(1, 2, 3))
def get_cutset_from_path(path_array, adjacency, source_nodes, dest_nodes):
    """
    Check if a path forms a cut set in a graph.

    Args:
        path_array: binary array of length num_edges indicating which edges are in the path
        adjacency: adjacency matrix of the graph (num_nodes x num_nodes)

    Returns:
        bool: True if the path forms a cut set
    """
    # Remove path edges from adjacency matrix
    # First convert path_array (edge list) to adjacency format
    num_nodes = adjacency.val.shape[0]

    # Create adjacency matrix for graph defined by path_array
    path_adjacency = edges_to_adjacency(path_array, source_nodes, dest_nodes, num_nodes)
    
    # Add in all the edges that connect any of the path nodes to create a subgraph
    subgraph_adjacency = make_complete_subgraph(path_adjacency, adjacency)
    
    # Get the "cutset" (might not be a cutset) links that separate the subgraph from the rest, in matrix form
    cutset_adjacency = find_cutset_adj(subgraph_adjacency, adjacency)
    
    # Remove cutset edges from original adjacency
    remaining_graph = adjacency.val * (1 - cutset_adjacency)

    # Next we want to find the partitions
    # Start from node 0 and find all reachable nodes
    # Initialize reachability vector (1 for node 0, 0 for others)
    reachable = jnp.zeros(num_nodes)
    reachable = reachable.at[0].set(1)
    
    # Keep multiplying by adjacency and taking any positive value
    # Until no new nodes are reached
    def update_reachable(reachable, _):
        new_reachable = jnp.matmul(reachable, remaining_graph)
        return jnp.where(new_reachable > 0, 1, reachable), None
    
    # Do this num_nodes times to ensure we reach all possible nodes
    partition1, _ = jax.lax.scan(update_reachable, 
                                reachable, 
                                jnp.arange(num_nodes-1))
    partition1 = partition1.astype(jnp.int32)
    partition2 = 1 - partition1
    return partition1, partition2


@partial(jax.jit, static_argnums=(2, 3))
def find_cutset_edges(p1, p2, source_nodes, dest_nodes):
    """
    Find the cut set between two subgraphs using JAX operations.

    Args:
        p1: Binary array indicating nodes in first subgraph (shape: [n_nodes])
        p2: Binary array indicating nodes in second subgraph (shape: [n_nodes])
        source_nodes: Array of source node indices for each edge
        dest_nodes: Array of destination node indices for each edge

    Returns:
        cut_set: Binary array indicating which edges are in the cut set,
                aligned with the provided edge_list ordering
    """

    # Create the output array based on edge_list ordering
    def is_cut_edge(edge):
        u, v = edge
        # Check both directions since we don't know edge orientation
        return jnp.logical_or(
            jnp.logical_and(p1[u], p2[v]),
            jnp.logical_and(p1[v], p2[u])
        )
    offset = -jnp.min(jnp.concatenate([source_nodes.val, dest_nodes.val]))
    # Map over edge_list to create correctly ordered binary array
    cut_set = jax.vmap(is_cut_edge)(jnp.stack([source_nodes.val+offset, dest_nodes.val+offset], axis=1))

    return cut_set.astype(jnp.int32)
    

@partial(jax.jit, static_argnums=(1,))
def find_cutset_adj(subgraph_matrix, full_matrix):
    # Find nodes in subgraph (any connection in subgraph matrix)
    in_subgraph = (subgraph_matrix.sum(axis=0) + subgraph_matrix.sum(axis=1)) > 0
    
    # Create masks for inside/outside
    inside = in_subgraph.reshape(-1, 1)  # column vector
    outside = ~in_subgraph.reshape(1, -1)  # row vector
    
    # Cutset is edges in full graph between inside and outside nodes
    cutset = jnp.logical_and(full_matrix.val,
                           jnp.logical_or(
                               jnp.logical_and(inside, outside),
                               jnp.logical_and(outside.T, inside.T)
                           ))
    return cutset


@partial(jax.jit, static_argnums=(1, 2, 3, 4))
def calculate_congestion(partition_mask, adjacency_matrix, traffic_matrix, source_nodes, dest_nodes):
    """
    Calculate congestion for a single cut defined by a binary partition mask.
    
    Args:
        partition_mask: binary array of shape (n_nodes,) where 1 indicates node is in set S
        adjacency_matrix: (n_nodes, n_nodes) binary matrix
        traffic_matrix: (n_nodes, n_nodes) float matrix
        source_nodes: array of source node indices for each edge
        dest_nodes: array of destination node indices for each edge
    
    Returns:
        congestion: scalar congestion value
        cut_size: number of edges in the cut
    """
    num_nodes = adjacency_matrix.shape[0]
    # Create masks for sets S and V-S
    partition1 = partition_mask
    partition2 = 1 - partition_mask
    
    # Find edges in cut set
    edges = find_cutset_edges(partition1, partition2, source_nodes, dest_nodes)
    cut_matrix = edges_to_adjacency(edges, source_nodes, dest_nodes, num_nodes)
    cut_size = jnp.sum(cut_matrix)
    
    # Calculate total traffic across cut
    traffic_across_cut = jnp.sum(
        traffic_matrix.val * (partition1[:, None] * partition2[None, :])
    )
    # Avoid zero division
    congestion = jnp.where(cut_size > 0, traffic_across_cut / cut_size, 0.)
    
    # # Next we must check connectivity of both partitions
    # power1 = jnp.matmul(jnp.diag(partition1), adjacency_matrix.val)
    # power2 = jnp.matmul(jnp.diag(partition2), adjacency_matrix.val)
    # # Repeatedly multiply adjacency matrices by themselves to find all possible paths
    # # This will reveal if nodes can reach each other within the partition
    # power1 = jnp.linalg.matrix_power(power1, adjacency_matrix.val.shape[0] - 1)
    # power2 = jnp.linalg.matrix_power(power2, adjacency_matrix.val.shape[0] - 1)
    #
    # # Check if nodes in each partition can reach enough other nodes
    # Number of reachable nodes should be at least twice partition size
    jax.debug.print("adj {}", adjacency_matrix.val)
    jax.debug.print("cut {}", cut_matrix)
    partitioned_adj = adjacency_matrix.val - cut_matrix
    jax.debug.print("padj {}", partitioned_adj)
    masked1 = jnp.where(jnp.outer(partition1, partition1) > 0, partitioned_adj, 0)
    masked2 = jnp.where(jnp.outer(partition2, partition2) > 0, partitioned_adj, 0)
    jax.debug.print("Outer {} {}", jnp.outer(partition1, partition1), jnp.outer(partition2, partition2))
    jax.debug.print("m1 {} m2 {}", masked1, masked2)
    jax.debug.print("p1 {} p2 {}", partition1, partition2)
    # TODO - to check connectivity, mask the columns of adjacency corresponding to the other partition to 0, then sum rows and check that each row has at least 1
    check1 = jnp.where(partition1 > 0, jnp.sum(masked1, axis=0) > 0, True)
    check2 = jnp.where(partition2 > 0, jnp.sum(masked2, axis=0) > 0, True)
    jax.debug.print("check1 {} check2 {}", check1, check2)
    connected1 = jnp.all(check1)
    connected2 = jnp.all(check2)
    # check_matrix_1 = jnp.matmul(jnp.diag(partition1), partitioned_adj)
    # check_matrix_2 = jnp.matmul(jnp.diag(partition2), partitioned_adj)
    # connected1 = jnp.sum(power1 > 0) >= jnp.sum(partition1)**2 - 1 # - jnp.sum(partition1)
    # connected2 = jnp.sum(power2 > 0) >= jnp.sum(partition2)**2 - 1 # - jnp.sum(partition2)
    both_connected = (connected1 & connected2).astype(jnp.float32)
    #jax.debug.print("{} {} {} {} {} {} {} {} {}", connected1, connected2, both_connected, partition1, partition2, congestion, cut_matrix, power1, power2)
    return congestion * both_connected


@partial(jax.jit, static_argnums=(0, 1))
def generate_gray_code_masks(n_nodes: int, max_batch_size: int, start: int):
    """Generate batch_size Gray code masks with static shapes, shifted by batch_idx.

    Args:
        n_nodes: Number of nodes in the graph (static)
        max_batch_size: Size of batch to generate (static)
        batch_idx: Which batch we're on (static for each call)
    """
    # Create static sequence and shift it by batch_idx * max_batch_size (i.e. start)
    base_sequence = jnp.arange(max_batch_size) + start

    # Convert to Gray code: n ^ (n >> 1)
    gray_numbers = base_sequence ^ (base_sequence >> 1)

    # Convert to binary matrix with static shape
    powers_of_two = jnp.power(2, jnp.arange(n_nodes - 1, -1, -1))
    numbers = gray_numbers[:, jnp.newaxis]
    masks = (numbers // powers_of_two) % 2
    return masks


@partial(jax.jit, static_argnums=(1, 2, 3, 4), donate_argnums=(0,))
def calculate_congestion_batch(partition_masks_batch, adjacency_matrix, traffic_matrix, source_nodes, dest_nodes):
    return jax.vmap(calculate_congestion, in_axes=(0, None, None, None, None))(partition_masks_batch, adjacency_matrix, traffic_matrix, source_nodes, dest_nodes)


def find_congested_cuts_exhaustive(
        start,
        num_iterations,
        num_batches_per_iteration,
        adj_matrix,
        traf_matrix,
        num_nodes,
        top_k,
        max_batch_size,
        source_nodes,
        dest_nodes,
):
    """
    Find the most congested cuts in the graph.
    
    Args:
        start: starting index for this process
        num_iterations: number of iterations to run
        num_batches_per_iteration: number of batches to run per iteration
        adj_matrix: (n_nodes, n_nodes) adjacency matrix
        traf_matrix: (n_nodes, n_nodes) traffic demand matrix
        num_nodes: number of nodes (must be static for JIT)
        top_k: number of top cuts to keep if no threshold specified
        max_batch_size: maximum batch size for JIT compilation
    """

    def find_congested_cuts_iter(_, i):

        # Create sequence to scan over
        batch_indices = jnp.arange(num_batches_per_iteration) + i

        def batch_eval_sort(_, j):
            j = j * max_batch_size
            new_masks = generate_gray_code_masks(num_nodes, max_batch_size, j)
            new_congestions = calculate_congestion_batch(new_masks, adj_matrix, traf_matrix, source_nodes, dest_nodes)
            # Sort by congestion, get top k
            top_indices = jnp.argsort(new_congestions)[-top_k:]
            new_congestions = new_congestions[top_indices]
            new_masks = new_masks[top_indices]
            return None, (new_congestions, new_masks)

        _, (result_congestions, result_masks) = jax.lax.scan(
            batch_eval_sort,
            None,
            batch_indices
        )

        # Sort by congestion and keep top_k
        result_congestions = result_congestions.reshape((-1,))
        sorted_indices = jnp.argsort(result_congestions)[-top_k:]
        congestions_iter = result_congestions[sorted_indices]
        masks_iter = result_masks.reshape((-1, num_nodes))[sorted_indices]
        return None, (congestions_iter, masks_iter)

    _, (congestions, masks) = jax.lax.scan(
        find_congested_cuts_iter,
        None,
        (jnp.arange(num_iterations) + start) * num_batches_per_iteration,
    )
    
    congestions = congestions.reshape((-1,))
    masks = masks.reshape((-1, num_nodes))
    partition1 = masks
    partition2 = 1 - masks
    
    # None at the end to align with return from simple method
    return congestions, partition1, partition2
    
    
@partial(jax.jit, static_argnums=(0, 1, 2, 3, 4))
def find_congested_cuts_simple(path_link_array, source_nodes, dest_nodes, adjacency_matrix, traffic_matrix):

    def get_cutset_partitions_and_congestion(_, i):
        path = path_link_array.val[i]
        p1, p2 = get_cutset_from_path(path, adjacency_matrix, source_nodes, dest_nodes)
        congestion = calculate_congestion(p1, adjacency_matrix, traffic_matrix, source_nodes, dest_nodes)
        return None, (congestion, p1, p2)
        
    path_indices = jnp.arange(path_link_array.shape[0])
    _, (congestions, partition1, partition2) = jax.lax.scan(get_cutset_partitions_and_congestion, None, path_indices)
    return congestions, partition1, partition2
    
    
def main_jax(argv):

    if FLAGS.DISABLE_JIT:
        jax.config.update("jax_disable_jit", True)
        jax.numpy.set_printoptions(threshold=sys.maxsize, linewidth=220)
    graph = make_graph(FLAGS.topology_name, FLAGS.topology_directory)
    edges = sorted(graph.edges())
    source_nodes = jnp.array([edge[0] for edge in edges])
    destination_nodes = jnp.array([edge[1] for edge in edges])
    adj_matrix = nx.adjacency_matrix(graph, weight="").todense()
    _, params = define_env(FLAGS)
    traffic_matrix = get_weighted_traffic_matrix(graph, params)
    traffic_matrix = normalise_traffic_matrix(traffic_matrix)
    traffic_matrix = HashableArrayWrapper(traffic_matrix)
    adj_matrix = HashableArrayWrapper(jnp.array(adj_matrix))
    source_nodes = HashableArrayWrapper(source_nodes)
    destination_nodes = HashableArrayWrapper(destination_nodes)
    top_k = FLAGS.CUTSET_TOP_K


    if FLAGS.CUTSET_EXHAUSTIVE:
        total_combinations = 2 ** params.num_nodes
        parallel_processes = FLAGS.NUM_ENVS
        batch_size = min(FLAGS.CUTSET_BATCH_SIZE, total_combinations)
        batches_per_process = math.ceil(total_combinations / parallel_processes / batch_size)
        iterations_per_process = min(FLAGS.CUTSET_ITERATIONS, batches_per_process)
        batches_per_iteration = math.ceil(total_combinations / parallel_processes / iterations_per_process / batch_size)
        print(f"Top k: {top_k}")
        print(f"Num Nodes: {params.num_nodes}")
        print(f"Parallel processes: {parallel_processes}")
        print(f"Total cut-set combinations: {total_combinations}")
        print(f"Max batch size: {batch_size}")
        print(f"Total batches: {jnp.ceil(total_combinations / batch_size).astype(jnp.int32)}")
        print(f"Batches per process: {batches_per_process}")
        print(f"Batches per iteration: {batches_per_iteration}")
        print(f"Iterations per process: {iterations_per_process}")
        starts = jnp.arange(parallel_processes) * iterations_per_process * batch_size
        starts = jax.device_put(starts, jax.devices()[int(FLAGS.VISIBLE_DEVICES)])
        if FLAGS.DISABLE_JIT:
            heavy_cut_sets = find_congested_cuts_exhaustive(
                starts, iterations_per_process, batches_per_iteration, adj_matrix, traffic_matrix, params.num_nodes,
                top_k, batch_size, source_nodes, destination_nodes
            )
        else:
            with TimeIt("COMPILATION:"):
                func = jax.jit(
                    jax.vmap(
                        find_congested_cuts_exhaustive,
                        in_axes=(0, None, None, None, None, None, None, None, None, None)
                    ),
                static_argnums=(1, 2, 3, 4, 5, 6, 7, 8, 9)).lower(
                    starts, iterations_per_process, batches_per_iteration, adj_matrix, traffic_matrix,
                    params.num_nodes, top_k, batch_size, source_nodes, destination_nodes
                ).compile()
            # Find the heavy cut-sets
            with TimeIt("EXECUTION:", frames=total_combinations):
                heavy_cut_sets = func(starts)
    else:
        if FLAGS.DISABLE_JIT:
            heavy_cut_sets = find_congested_cuts_simple(
                params.path_link_array, source_nodes, destination_nodes, adj_matrix, traffic_matrix
            )
        else:
            with TimeIt("COMPILATION:"):
                func = jax.jit(find_congested_cuts_simple, static_argnums=(0, 1, 2, 3, 4)).lower(
                    params.path_link_array, source_nodes, destination_nodes, adj_matrix, traffic_matrix
                ).compile()
            with TimeIt("EXECUTION:"):
                heavy_cut_sets = func()

    # Reshape arrays to flatten all but the last dimension of the partitions and all dimensions of the congestions
    # Reshape and sort
    congestions = jnp.reshape(heavy_cut_sets[0], (-1,))
    partition1 = jnp.reshape(heavy_cut_sets[1], (-1, params.num_nodes))
    partition2 = jnp.reshape(heavy_cut_sets[2], (-1, params.num_nodes))
    cutset_edges = jax.vmap(find_cutset_edges, in_axes=(0, 0, None, None))(partition1, partition2, source_nodes, destination_nodes)
    # Find duplicate rows in cutset_edges and remove one of each duplicate pair
    unique_cutset_edges, unique_indices = jnp.unique(cutset_edges, axis=0, return_index=True)
    cutset_edges = cutset_edges[unique_indices]
    congestions = congestions[unique_indices]
    partition1 = partition1[unique_indices]
    partition2 = partition2[unique_indices]
    top_k_indices = jnp.argsort(congestions)[-top_k:]
    heavy_cut_sets = {
        'congestion': congestions[top_k_indices],
        'partition1': partition1[top_k_indices],
        'partition2': partition2[top_k_indices],
        'cutset_edges': cutset_edges[top_k_indices],
    }
    print(heavy_cut_sets)
    for cutset in heavy_cut_sets["cutset_edges"]:
        print([int(link) for link in cutset])

    # TODO - simulate traffic on each cut set
    # Each cut set is defined by partition1, partition2, cutset_edges, link_slot_array, link_slot_departure_array
    # We want to generate a request, then vmap over the cutsets with the same request


if __name__ == "__main__":
    app.run(main_jax)