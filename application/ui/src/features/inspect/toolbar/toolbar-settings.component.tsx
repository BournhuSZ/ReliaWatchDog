// Copyright (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from 'react';

import { Button, Content, Dialog, DialogTrigger, Divider, Flex, Heading, Switch, Text } from '@geti-ui/ui';

const DISPLAY_MODE_STORAGE_KEY = 'anomalib-studio.display-mode';

type DisplayMode = 'light' | 'dark';

const getInitialDisplayMode = (): DisplayMode => {
    if (typeof window === 'undefined') {
        return 'dark';
    }

    const storedValue = window.localStorage.getItem(DISPLAY_MODE_STORAGE_KEY);

    if (storedValue === 'light' || storedValue === 'dark') {
        return storedValue;
    }

    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
};

const applyDisplayMode = (displayMode: DisplayMode) => {
    if (typeof document === 'undefined') {
        return;
    }

    document.documentElement.dataset.displayMode = displayMode;
    document.documentElement.style.colorScheme = displayMode;

    // ThemeProvider uses Spectrum classes. Keep them in sync with the user preference.
    document.body.classList.toggle('spectrum--dark', displayMode === 'dark');
    document.body.classList.toggle('spectrum--light', displayMode === 'light');
};

export const ToolbarSettings = () => {
    const [displayMode, setDisplayMode] = useState<DisplayMode>(getInitialDisplayMode);

    useEffect(() => {
        applyDisplayMode(displayMode);
        window.localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, displayMode);
    }, [displayMode]);

    return (
        <DialogTrigger type='popover'>
            <Button variant='secondary'>Settings</Button>
            <Dialog minWidth={'size-3000'}>
                <Content>
                    <Flex direction='column' gap='size-150'>
                        <Heading level={4}>Basic settings</Heading>
                        <Divider size='S' />
                        <Flex alignItems='center' justifyContent='space-between' gap='size-200'>
                            <Text>Dark display mode</Text>
                            <Switch
                                aria-label='dark display mode'
                                isSelected={displayMode === 'dark'}
                                onChange={(isSelected) => setDisplayMode(isSelected ? 'dark' : 'light')}
                            >
                                {displayMode === 'dark' ? 'Dark' : 'Light'}
                            </Switch>
                        </Flex>
                        <Text UNSAFE_style={{ color: 'var(--spectrum-global-color-gray-600)' }}>
                            More settings will be added here.
                        </Text>
                    </Flex>
                </Content>
            </Dialog>
        </DialogTrigger>
    );
};