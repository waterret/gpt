#
#    GPT - Grid Python Toolkit
#    Copyright (C) 2020  Christoph Lehner (christoph.lehner@ur.de, https://github.com/lehner/gpt)
#                  2020  Daniel Richtmann (daniel.richtmann@ur.de)
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License along
#    with this program; if not, write to the Free Software Foundation, Inc.,
#    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
import gpt, cgpt, sys, numpy


def create_links(A, fmat, basis, params):
    # NOTE: we expect the blocks in the basis vectors
    # to already be orthogonalized!
    # parameters
    hermitian = params["hermitian"]
    savelinks = params["savelinks"]
    assert not (hermitian and not savelinks)

    # verbosity
    verbose = gpt.default.is_verbose("coarsen")

    # setup timings
    t = gpt.timer("coarsen")
    t("setup")

    # get grids
    f_grid = basis[0].grid
    c_grid = A[0].grid

    # directions/displacements we coarsen for
    dirs = [1, 2, 3, 4] if f_grid.nd == 5 else [0, 1, 2, 3]
    disp = +1
    dirdisps_full = list(zip(dirs * 2, [+1] * 4 + [-1] * 4))
    dirdisps_forward = list(zip(dirs, [disp] * 4))
    nhops = len(dirdisps_full)
    selflink = nhops

    # setup fields
    Mvr = [gpt.lattice(basis[0]) for i in range(nhops)]
    tmp = gpt.lattice(basis[0])
    oproj = gpt.complex(c_grid)
    selfproj = gpt.vcomplex(c_grid, len(basis))

    # setup masks
    onemask, blockevenmask, blockoddmask = (
        gpt.complex(f_grid),
        gpt.complex(f_grid),
        gpt.complex(f_grid),
    )
    dirmasks = [gpt.complex(f_grid) for p in range(nhops)]

    # auxilliary stuff needed for masks
    t("masks")
    onemask[:] = 1.0
    coor = gpt.coordinates(blockevenmask)
    block = numpy.array(f_grid.ldimensions) / numpy.array(c_grid.ldimensions)
    block_cb = coor[:, :] // block[:]

    # fill masks for sites within even/odd blocks
    gpt.make_mask(blockevenmask, numpy.sum(block_cb, axis=1) % 2 == 0)
    blockoddmask @= onemask - blockevenmask

    # fill masks for sites on borders of blocks
    dirmasks_forward_np = coor[:, :] % block[:] == block[:] - 1
    dirmasks_backward_np = coor[:, :] % block[:] == 0
    for p, d in enumerate(dirs):
        gpt.make_mask(dirmasks[p], dirmasks_forward_np[:, p])
        gpt.make_mask(dirmasks[4 + p], dirmasks_backward_np[:, p])

    # save applications of matrix and coarsening if possible
    dirdisps = dirdisps_forward if savelinks else dirdisps_full

    for i, vr in enumerate(basis):
        # apply directional hopping terms
        # this triggers len(dirdisps) comms -> TODO expose DhopdirAll from Grid
        # BUT problem with vector<Lattice<...>> in rhs
        t("apply_hop")
        [fmat.Mdir(Mvr[p], vr, d, fb) for p, (d, fb) in enumerate(dirdisps)]

        # coarsen directional terms + write to link
        for p, (d, fb) in enumerate(dirdisps):
            for j, vl in enumerate(basis):
                t("coarsen_hop")
                gpt.block.maskedInnerProduct(oproj, dirmasks[p], vl, Mvr[p])

                t("copy_hop")
                A[p][:, :, :, :, j, i] = oproj[:]

        # fast diagonal term: apply full matrix to both block cbs separately and discard hops into other cb
        t("apply_self")
        tmp @= (
            blockevenmask * fmat.M * vr * blockevenmask
            + blockoddmask * fmat.M * vr * blockoddmask
        )

        # coarsen diagonal term
        t("coarsen_self")
        gpt.block.project(selfproj, tmp, basis)

        # write to self link
        t("copy_self")
        A[selflink][:, :, :, :, :, i] = selfproj[:]

        if verbose:
            gpt.message("Coarsening of vector %d finished" % i)

    # communicate opposite links
    if savelinks:
        t("comm")
        for p, (d, fb) in enumerate(dirdisps_forward):
            dd = d + 4
            shift_disp = fb * -1
            Atmp = gpt.copy(A[d])
            if not hermitian:
                nbasis = len(basis)
                assert nbasis % 2 == 0
                nb = nbasis // 2
                Atmp[:, :, :, :, 0:nb, nb:nbasis] *= -1.0  # upper right block
                Atmp[:, :, :, :, nb:nbasis, 0:nb] *= -1.0  # lower left block
            A[dd] @= gpt.adj(gpt.cshift(Atmp, d, shift_disp))

    t()

    if verbose:
        t.print()


def recreate_links(A, fmat, basis):
    create_links(A, fmat, basis)
